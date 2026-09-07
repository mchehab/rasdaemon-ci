# SPDX-License-Identifier: GPL-2.0-only
"""Check consumer side effects separately, using the event test's single injection."""

import json
import os
import socket
import subprocess
import threading


class ConsumerChecks:
    """Observe real daemon outputs without substituting its consumer functions."""

    def __init__(self, scenario) -> None:
        self.scenario = scenario
        self.reports: list[bytes] = []
        self.listener: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self.trigger_path = scenario.paths["database"] + ".trigger.json"
        self.cpu_online: str | None = None

    def receive(self) -> None:
        """Drain ABRT's Unix socket so reporting cannot block the event loop."""
        while not self.stopping.is_set():
            try:
                connection, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return

            data = bytearray()
            with connection:
                connection.settimeout(2)
                try:
                    while chunk := connection.recv(65536):
                        data.extend(chunk)
                except (socket.timeout, OSError):
                    pass
            self.reports.append(bytes(data))

    def prepare(self) -> None:
        """Install an ABRT protocol sink and a real executable trigger in the VM."""
        backend = self.scenario.scenario.get("backend")
        if backend:
            self.prepare_database(backend)
        if self.scenario.scenario.get("producer") == "pfa":
            prefix = "ROW" if "row" in self.scenario.scenario["name"] else "PAGE"
            self.scenario.environment[prefix + "_CE_ACTION"] = "hard"
            self.scenario.environment[prefix + "_CE_THRESHOLD"] = "1"
        if self.scenario.scenario["name"] == "ghes-aer":
            self.scenario.environment.update(BMC_GENERIC_ENABLE="yes")
        if self.scenario.scenario["name"] == "consumer-arm-sel":
            self.scenario.environment.update(AMPERE_OEM_SEL_ENABLE="yes",
                                             OPENBMC_UNIFIED_SEL_ENABLE="yes")
        if self.scenario.scenario["name"] == "ghes-arm":
            with open("/sys/devices/system/cpu/cpu1/online", encoding="ascii") as stream:
                self.cpu_online = stream.read().strip()
            if self.cpu_online != "1":
                raise RuntimeError("CPU isolation requires an initially online CPU1")
            self.scenario.environment.update(CPU_ISOLATION_ENABLE="yes", CPU_CE_THRESHOLD="1",
                                             CPU_ISOLATION_LIMIT="1")
        if self.scenario.scenario["name"] != "memory-failure":
            return

        os.makedirs("/var/run/abrt", exist_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind("/var/run/abrt/abrt.socket")
        except OSError:
            listener.close()
            raise
        self.listener = listener
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        self.thread = threading.Thread(target=self.receive, daemon=True)
        self.thread.start()
        script = self.scenario.paths["database"] + ".trigger"
        code = ("#!/usr/bin/python3\nimport json, os\n"
                f"with open({self.trigger_path!r}, 'w', encoding='utf-8') as stream:\n"
                "    json.dump(dict(os.environ), stream)\n")
        with open(script, "w", encoding="utf-8") as stream:
            stream.write(code)
        os.chmod(script, 0o700)
        self.scenario.environment["MEM_FAIL_TRIGGER"] = script
        self.scenario.environment["POISON_STAT_THRESHOLD"] = "0"

    def prepare_database(self, backend: str) -> None:
        """Provision a private real database server inside the disposable VM."""
        environment = self.scenario.environment
        environment["RASDAEMON_DB_BACKEND"] = backend
        if backend == "mysql":
            commands = [["systemctl", "start", "mariadb"],
                        ["mariadb", "-e", "CREATE DATABASE ras_ci; "
                         "CREATE USER 'ras_ci'@'localhost' IDENTIFIED BY 'ras_ci'; "
                         "GRANT ALL ON ras_ci.* TO 'ras_ci'@'localhost';"]]
            environment.update(RAS_MYSQL_USER="ras_ci", RAS_MYSQL_PASSWORD="ras_ci",
                               RAS_MYSQL_DATABASE="ras_ci",
                               RAS_MYSQL_SOCKET="/run/mysqld/mysqld.sock")
        else:
            commands = [["systemctl", "start", "postgresql"],
                        ["runuser", "-u", "postgres", "--", "psql", "-v", "ON_ERROR_STOP=1",
                         "-c", "CREATE USER ras_ci WITH PASSWORD 'ras_ci';"],
                        ["runuser", "-u", "postgres", "--", "createdb", "-O", "ras_ci", "ras_ci"]]
            environment.update(RAS_PG_HOST="127.0.0.1", RAS_PG_USER="ras_ci",
                               RAS_PG_PASSWORD="ras_ci", RAS_PG_DATABASE="ras_ci",
                               RAS_PG_SCHEMA="ras_ci")
        transcript = self.scenario.evidence.setdefault("database_setup", [])
        for command in commands:
            completed = subprocess.run(command, check=False, text=True, timeout=60,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            transcript.append({"command": command, "output": completed.stdout,
                               "returncode": completed.returncode})
            if completed.returncode:
                raise RuntimeError(f"{backend} server setup failed: {completed.stdout}")

    def records(self) -> list[dict]:
        """Read the selected SQL backend through the installed report interface."""
        command = ["/usr/sbin/ras-mc-ctl", "database", "--errors", "--json",
                   "--table", self.scenario.scenario["table"]]
        completed = subprocess.run(command, check=False, text=True, timeout=10,
                                   env=dict(os.environ, **self.scenario.environment),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.scenario.evidence["database_query"] = {"command": command,
                                                    "output": completed.stdout,
                                                    "returncode": completed.returncode}
        if completed.returncode:
            return []
        document = json.loads(completed.stdout)
        return [row["fields"] for row in document.get("records", [])
                if row.get("table") == self.scenario.scenario["table"]]

    def close(self) -> None:
        """Stop only the receiver and socket owned by this scenario."""
        if self.cpu_online is not None:
            path = "/sys/devices/system/cpu/cpu1/online"
            with open(path, encoding="ascii") as stream:
                self.scenario.evidence["cpu1_after_injection"] = stream.read().strip()
            with open(path, "w", encoding="ascii") as stream:
                stream.write(self.cpu_online + "\n")
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.listener:
            self.listener.close()
            self.listener = None
            os.unlink("/var/run/abrt/abrt.socket")

    def result(self, name: str, passed: bool, reason: str, evidence: dict) -> None:
        """Keep consumer outcomes separate from event detection outcomes."""
        self.scenario.results.add("consumer-" + name, "passed" if passed else "failed",
                                  reason, evidence, kernel="N/A",
                                  rasdaemon="PASS" if passed else "FAIL")

    def check(self, event_status: str) -> None:
        """Validate protocol content, trigger arguments and committed report data."""
        if self.scenario.scenario["name"] in ("ghes-aer", "consumer-arm-sel"):
            self.check_sel(event_status)
        if self.scenario.scenario["name"] == "ghes-arm":
            evidence = self.scenario.evidence
            valid = evidence.get("cpu1_after_injection") == "0"
            self.result("cpu-fault-isolation", event_status == "passed" and valid,
                        "CPU1 must transition from online to offline after corrected ARM errors",
                        {"before": self.cpu_online, "after": evidence.get("cpu1_after_injection"),
                         "output": evidence.get("rasdaemon_output", "")})
            vendor = self.scenario.scenario["vendor_hex"]
            valid = any(row.get("vendor_info") == vendor for row in evidence["rows"])
            output = evidence.get("rasdaemon_output", "")
            valid = valid and "0x1234" in output and "0x5678" in output
            self.scenario.results.add("arm-vendor-data",
                                      "passed" if event_status == "passed" and valid else "failed",
                                      "ARM vendor data must be retained and decoded",
                                      {"vendor_data": vendor, "output": output}, kernel="N/A",
                                      rasdaemon="PASS" if event_status == "passed" and valid else "FAIL")
        if self.scenario.scenario["name"] != "memory-failure":
            return

        evidence = self.scenario.evidence
        rows = evidence["rows"]
        passed = event_status == "passed"
        pfn = f"{evidence.get('pfn', 0):#x}"
        reports = [report.decode("utf-8", errors="replace") for report in self.reports]
        valid = any(report.startswith("PUT / HTTP/1.1\r\n\r\n") and
                    "TYPE=ras\0" in report and f"pfn={pfn}" in report
                    for report in reports)
        self.result("abrt-report", passed and valid,
                    "ABRT report must contain the injected page's PFN",
                    {"reports": [report.replace("\0", "\n") for report in reports]})

        trigger = {}
        if os.path.isfile(self.trigger_path):
            with open(self.trigger_path, encoding="utf-8") as stream:
                trigger = json.load(stream)
        valid = trigger.get("PFN") == pfn and bool(trigger.get("ACTION_RESULT"))
        self.result("trigger", passed and valid,
                    "Memory-failure trigger must receive the injected PFN and action result",
                    {"environment": trigger})

        output = evidence.get("rasdaemon_output", "")
        valid = "Poison page statistics exceeded threshold:" in output
        self.result("poison-page-stat", passed and valid,
                    "Poison statistics must report a nonzero count after poisoning",
                    {"output": output})
        self.result("sqlite3", passed and bool(rows),
                    "SQLite must commit the injected memory-failure record",
                    {"database": self.scenario.paths["database"], "rows": rows})
        self.check_report(passed, pfn)

    def check_sel(self, event_status: str) -> None:
        """Read actual QEMU BMC records and verify each consumer's wire format."""
        path = self.scenario.paths["database"] + ".sel"
        command = ["ipmitool", "sel", "writeraw", path]
        evidence = {"command": command}
        records = []
        try:
            completed = subprocess.run(command, check=False, text=True, timeout=30,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            evidence.update(output=completed.stdout, returncode=completed.returncode)
            if completed.returncode == 0:
                with open(path, "rb") as stream:
                    raw = stream.read()
                if len(raw) % 16 == 0:
                    records = [raw[offset:offset + 16] for offset in range(0, len(raw), 16)]
        except (OSError, subprocess.TimeoutExpired) as error:
            evidence["error"] = str(error)
        evidence["records"] = [record.hex(" ") for record in records]
        bdf = self.scenario.evidence.get("bdf", "0000:00:00.0")
        segment, bus, devfn = bdf.split(":")
        device, function = devfn.split(".")
        bus_number = int(bus, 16)
        devfn_number = int(device, 16) * 8 + int(function, 16)
        segment_number = int(segment, 16)
        expected = {
            "bmc-generic": {2: 0x02, 7: 1, 9: 4, 10: 0x13, 12: 0x6f,
                            13: 0xa7, 14: bus_number, 15: devfn_number},
            "ampere-oem-sel": {2: 0xc0, 7: 0x3a, 8: 0xcd, 10: 0xc0, 11: 0xbf,
                               12: segment_number & 255, 13: segment_number >> 8,
                               14: bus_number, 15: devfn_number},
            "openbmc-unified-sel": {2: 0xfb, 3: 0x20, 8: 1, 10: devfn_number,
                                    11: bus_number, 12: 1, 14: 0xff, 15: 2},
        }
        for name, fields in expected.items():
            arm_consumer = name != "bmc-generic"
            if arm_consumer != (self.scenario.scenario["name"] == "consumer-arm-sel"):
                continue
            matches = [record for record in records
                       if all(record[index] == value for index, value in fields.items())]
            self.result(name, event_status == "passed" and len(matches) == 1,
                        "BMC SEL must contain exactly one matching corrected AER record",
                        dict(evidence, bdf=bdf, expected_bytes=fields))

    def check_report(self, event_passed: bool, pfn: str) -> None:
        """Check the installed CLI's values, rather than merely its exit code."""
        command = ["/usr/sbin/ras-mc-ctl", "database", "--errors", "--json",
                   "--table", "memory_failure_event"]
        evidence = {"command": command}
        valid = False
        try:
            completed = subprocess.run(command, check=False, text=True, timeout=30,
                                       env=dict(os.environ, **self.scenario.environment),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            evidence.update(output=completed.stdout, returncode=completed.returncode)
            document = json.loads(completed.stdout)
            valid = completed.returncode == 0 and document.get("mode") == "errors" and any(
                row.get("table") == "memory_failure_event" and
                row.get("fields", {}).get("pfn") == pfn
                for row in document.get("records", []))
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            evidence["error"] = str(error)
        self.result("database-report", event_passed and valid,
                    "Installed database report must preserve the injected PFN", evidence)

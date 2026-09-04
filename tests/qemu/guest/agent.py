#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Mauro Carvalho Chehab <mchehab+huawei@kernel.org>

"""Build and exercise the payload supplied to a rasdaemon QEMU guest."""

import datetime
import contextlib
import ctypes
import glob
import json
import mmap
import os
import pathlib
import shutil
import signal
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
from typing import IO


RESULT_PORT = pathlib.Path(
    "/dev/virtio-ports/org.rasdaemon.test.0")
WORK_DIR = pathlib.Path("/var/tmp/rasdaemon-qemu")


class Results:
    """Collect scenario results while continuing after ordinary failures."""

    phase: str = "agent-starting"

    def __init__(self):
        self.tests = []

    def add(self, name, status, reason="", evidence=None, duration=0.0,
            kernel=None, rasdaemon=None):
        prerequisites = ("guest kernel lacks", "guest kernel exposes no",
                         "hardware-first MCE injection requires", "blkdebug guest disk was not discovered")

        if reason.startswith(prerequisites):
            status = "skipped"

        verdict = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[status]
        observed = (evidence or {}).get("sqlite_count", 0) > 0
        self.tests.append({
            "name": name,
            "status": status,
            "reason": reason,
            "evidence": evidence or {},
            "duration_seconds": round(duration, 3),
            "kernel": kernel or ("PASS" if observed else "SKIP"),
            "rasdaemon": rasdaemon or verdict,
        })
        print("ras-qemu-result: " + json.dumps(self.tests[-1], sort_keys=True), flush=True)
        self.progress(f"{name} {status}")

    @classmethod
    def progress(cls, message: str) -> None:
        """Write a short status line to the retained guest serial console."""
        cls.phase = message
        print("ras-qemu-agent: %s" % message, flush=True)

    def command(self, name, command, cwd=None, timeout=300, required=True,
                environment=None, expected_returncodes=(0,)):
        started = time.monotonic()
        self.progress("starting %s" % name)
        env = os.environ.copy()
        if environment:
            env.update(environment)
        try:
            completed = subprocess.run(
                command, cwd=cwd, env=env, check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            status = "failed" if required else "skipped"
            self.add(name, status, str(error), duration=time.monotonic() - started)
            self.progress("%s %s: %s" % (name, status, error))
            return False
        evidence = {
            "command": command,
            "returncode": completed.returncode,
            "output": completed.stdout[-32768:],
        }
        if completed.returncode not in expected_returncodes:
            status = "failed" if required else "skipped"
            self.add(name, status, "command returned %d" % completed.returncode,
                     evidence, time.monotonic() - started)
            self.progress("%s %s (return code %d)" %
                          (name, status, completed.returncode))
            return False
        self.add(name, "passed", evidence=evidence,
                 duration=time.monotonic() - started)
        self.progress("%s passed" % name)
        return True

    def document(self, profile):
        return {
            "format_version": 1,
            "profile": profile,
            "guest": {
                "architecture": os.uname().machine,
                "kernel": os.uname().release,
            },
            "failed": any(test["status"] == "failed" for test in self.tests),
            "tests": self.tests,
            "finished_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
        }


def run(command, **kwargs):
    """Run a short discovery command."""
    return subprocess.run(command, check=False, text=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, **kwargs)


def mount_payload():
    """Find and mount the read-only FAT disk containing the source payload."""
    devices = run(["lsblk", "-nrpo", "NAME,FSTYPE"]).stdout.splitlines()
    for line in devices:
        fields = line.split(None, 1)
        if len(fields) != 2 or fields[1].lower() not in ("vfat", "fat", "msdos"):
            continue
        mount_dir = pathlib.Path(tempfile.mkdtemp(prefix="ras-payload-"))
        mounted = run(["mount", "-o", "ro", fields[0], str(mount_dir)])
        if mounted.returncode:
            mount_dir.rmdir()
            continue
        if (mount_dir / ".ras-qemu-profile").is_file() and \
           ((mount_dir / "rasdaemon-install.tar").is_file() or
            (mount_dir / "meson.build").is_file()):
            return mount_dir
        run(["umount", str(mount_dir)])
        mount_dir.rmdir()
    raise RuntimeError("source payload disk was not found")


def safe_environment(database):
    """Return explicit settings which disable side-effectful actions."""
    return {
        "RASDAEMON_DB_BACKEND": "sqlite3",
        "RAS_SQLITE3_DATABASE": str(database),
        "BMC_GENERIC_ENABLE": "no",
        "OPENBMC_UNIFIED_SEL_ENABLE": "no",
        "AMPERE_OEM_SEL_ENABLE": "no",
        "PCIE_EDPC_ENABLE": "no",
        "CPU_ISOLATION_ENABLE": "no",
        "PAGE_CE_ACTION": "off",
        "ROW_CE_ACTION": "off",
        "MC_CE_TRIGGER": "",
        "MC_UE_TRIGGER": "",
        "AER_CE_TRIGGER": "",
        "AER_UE_TRIGGER": "",
        "MEM_FAIL_TRIGGER": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def ensure_tracefs(results):
    """Mount guest tracefs without treating an existing mount as an error."""
    target = pathlib.Path("/sys/kernel/tracing")
    target.mkdir(parents=True, exist_ok=True)
    if run(["mountpoint", "-q", str(target)]).returncode == 0:
        results.add("tracefs", "passed", evidence={"path": str(target)})
        return True
    return results.command("tracefs", ["mount", "-t", "tracefs", "tracefs",
                                       str(target)])


def guest_test_page(consume_poison: bool = False) -> tuple[int, int, int]:
    """Retain a touched page in a helper and return its physical address."""
    page_size = os.sysconf("SC_PAGE_SIZE")
    page = mmap.mmap(-1, page_size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    page[0] = 0xA5
    virtual = ctypes.addressof(ctypes.c_char.from_buffer(page))
    with open("/proc/self/pagemap", "rb", buffering=0) as stream:
        stream.seek((virtual // page_size) * 8)
        entry = struct.unpack("<Q", stream.read(8))[0]
    if not entry & (1 << 63):
        page.close()
        raise RuntimeError("allocated MCE target page is not present")
    pfn = entry & ((1 << 55) - 1)
    if not pfn:
        page.close()
        raise RuntimeError("guest pagemap did not disclose the page frame")
    physical = pfn * page_size + virtual % page_size
    old_mask = None
    if consume_poison:
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
    holder = os.fork()
    if holder == 0:
        try:
            if consume_poison:
                signal.sigwait({signal.SIGUSR1})
                # The kernel, rather than kill(2), generates the SIGBUS
                # when this helper accesses its poisoned anonymous page.
                _value = page[0]
            else:
                signal.pause()
        finally:
            os._exit(0)
    if old_mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
    page.close()
    return holder, physical, page_size


def wait_for_rows(database: str, table: str, timeout: float) -> bool:
    """Bound event delivery time without imposing a fixed sleep on success."""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if os.path.isfile(database):
            try:
                connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

                with contextlib.closing(connection):
                    if connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]:
                        return True
            except sqlite3.OperationalError:
                pass

        time.sleep(0.25)

    return False


def read_msr(cpu: int, register: int) -> str:
    """Read an x86 MSR for temporary MCE injection diagnostics."""
    path = f"/dev/cpu/{cpu}/msr"

    try:
        descriptor = os.open(path, os.O_RDONLY)

        try:
            value = struct.unpack("<Q", os.pread(descriptor, 8, register))[0]
        finally:
            os.close(descriptor)

        return f"{value:#018x}"
    except (OSError, struct.error) as error:
        return "unavailable: " + str(error)


def mca_state(bank: int) -> dict[str, str]:
    """Snapshot the global registers and one selected MCA bank."""
    base = 0x400 + bank * 4

    return {
        "mcg_cap": read_msr(0, 0x179),
        "mcg_status": read_msr(0, 0x17A),
        "mc_ctl": read_msr(0, base),
        "mc_status": read_msr(0, base + 1),
        "mc_addr": read_msr(0, base + 2),
        "mc_misc": read_msr(0, base + 3),
    }


def enabled_mce_bank() -> tuple[int, dict[str, str]]:
    """Choose the first CPU0 bank whose kernel control mask is nonzero."""
    controls = {}
    selected = None

    for filename in sorted(glob.glob(
            "/sys/devices/system/machinecheck/machinecheck0/bank[0-9]*")):
        name = os.path.basename(filename)

        try:
            value = read_text(filename).strip()
            controls[name] = value
            if int(value, 16) and selected is None:
                selected = int(name[4:])
        except (OSError, ValueError) as error:
            controls[name] = "unavailable: " + str(error)

    if selected is not None:
        return selected, controls

    raise RuntimeError("guest exposes no enabled CPU0 MCA bank: " + json.dumps(controls))


def mce_rows(database: str) -> list[dict]:
    """Return raw MCE register rows without creating a missing database."""
    if not os.path.isfile(database):
        return []

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

    with contextlib.closing(connection):
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT status, addr, misc, bank, cpu, mcgcap, mcgstatus "
            "FROM mce_record").fetchall()

    return [dict(row) for row in rows]


def mce_memory_smoke(results, build, environment):
    """Receive a host-injected x86 MCE and verify real rasdaemon."""
    trace_format = pathlib.Path(
        "/sys/kernel/tracing/events/mce/mce_record/format")
    trace_event = pathlib.Path(
        "/sys/kernel/tracing/instances/rasdaemon/events/mce/"
        "mce_record/enable")
    if os.uname().machine != "x86_64":
        results.add("mce-hardware-first", "failed",
                    "hardware-first MCE injection requires an x86_64 guest")
        return
    if not trace_format.is_file():
        results.add("mce-hardware-first", "failed",
                    "guest kernel lacks mce:mce_record")
        return
    poll_intervals = sorted(pathlib.Path(
        "/sys/devices/system/machinecheck").glob(
            "machinecheck*/check_interval"))
    if not poll_intervals:
        results.add("mce-hardware-first", "failed",
                    "guest kernel exposes no MCE polling interval control")
        return

    database = pathlib.Path(environment["RAS_SQLITE3_DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True)
    database.unlink(missing_ok=True)
    config = WORK_DIR / "rasdaemon.env"
    config.write_text("".join("%s=%s\n" % item
                              for item in sorted(environment.items())
                              if item[0] != "PYTHONDONTWRITEBYTECODE"),
                      encoding="utf-8")
    log_path = os.path.join(WORK_DIR, "mce-startup.log")
    observer = "/sys/kernel/tracing/instances/ras-test-mce"
    observer_enable = os.path.join(observer, "events/mce/mce_record/enable")
    observer_trace = os.path.join(observer, "trace")
    log = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [str(build / "rasdaemon"), "--foreground", "--record",
         "--config", str(config)],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **environment), start_new_session=True,
    )
    page_holder = None
    evidence = {}
    started = time.monotonic()
    try:
        events = [os.fspath(trace_event)]
        wait_daemon_ready(process, log_path, events)
        if process.poll() is not None:
            results.add("mce-hardware-first", "failed",
                        "rasdaemon exited before MCE injection",
                        {"output": read_text(log_path)[-32768:]})
            return
        try:
            trace_enabled = trace_event.read_text(encoding="ascii").strip()
        except OSError as error:
            results.add("mce-hardware-first", "failed",
                        "rasdaemon tracefs instance was not created",
                        {"error": str(error), "path": str(trace_event)})
            return
        if trace_enabled != "1":
            results.add("mce-hardware-first", "failed",
                        "rasdaemon did not enable mce:mce_record",
                        {"trace_enabled": trace_enabled})
            return
        os.mkdir(observer)

        with open(observer_enable, "w", encoding="ascii") as stream:
            stream.write("1\n")

        with open(observer_trace, "w", encoding="ascii") as stream:
            stream.write("")

        msr_module = run(["modprobe", "msr"])
        bank, controls = enabled_mce_bank()
        page_holder, physical, _page_size = guest_test_page()

        for interval in poll_intervals:
            interval.write_text("1\n", encoding="ascii")

        evidence.update({
            "bank": bank,
            "bank_controls": controls,
            "mca_before": mca_state(bank),
            "modprobe_msr": {"returncode": msr_module.returncode,
                             "output": msr_module.stdout},
            "physical_address": "%#x" % physical,
            "poll_intervals": {os.fspath(path): path.read_text(encoding="ascii").strip()
                               for path in poll_intervals},
            "trace_enabled_before_injection": trace_enabled,
        })
        results.progress("mce-ready physical=%#x bank=%d" % (physical, bank))
        # The host runner watches the retained console and injects through
        # QEMU's monitor only after this readiness marker is visible.
        wait_for_rows(os.fspath(database), "mce_record", 20)
        evidence["mca_after"] = mca_state(bank)
        evidence["kernel_trace"] = read_text(observer_trace)[-65536:]
        os.killpg(process.pid, signal.SIGTERM)
        _output, _unused = process.communicate(timeout=30)
        output = read_text(log_path)
        rows = mce_rows(os.fspath(database))
        expected_status = 0x9C00000000000090
        matching = [row for row in rows
                    if row["status"] & ((1 << 64) - 1) == expected_status and
                    row["addr"] == physical and row["bank"] == bank]
        evidence.update({
            "kernel_observed": "mce_record:" in evidence["kernel_trace"],
            "matching_rows": matching,
            "rows": rows,
            "sqlite_count": len(rows),
            "rasdaemon_output": output[-32768:],
            "rasdaemon_returncode": process.returncode,
        })
        status = "passed" if process.returncode == 0 and matching else "failed"
        reason = "" if status == "passed" else \
            "QEMU MCE did not produce a clean rasdaemon mce_record row"
        results.add("mce-hardware-first", status, reason, evidence,
                    time.monotonic() - started)
        if status == "passed":
            ras_mc_ctl = shutil.which("ras-mc-ctl") or "/usr/sbin/ras-mc-ctl"
            results.command(
                "mce-database-count",
                [ras_mc_ctl, "database", "--count",
                 "--table", "mce_record", "--json"],
                environment=environment,
            )
            results.command(
                "mce-database-report",
                [ras_mc_ctl, "database", "--errors",
                 "--table", "mce_record", "--json"],
                environment=environment,
            )
    except (OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        evidence["diagnostic_error"] = str(error)
        results.add("mce-hardware-first", "failed", str(error), evidence)
    finally:
        if page_holder is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(page_holder, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(page_holder, 0)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

        log.close()

        if os.path.isdir(observer):
            with contextlib.suppress(OSError):
                with open(observer_enable, "w", encoding="ascii") as stream:
                    stream.write("0\n")

                os.rmdir(observer)


def aer_smoke(results, build, environment):
    """Receive a QEMU PCIe AER error and verify real rasdaemon recording."""
    trace_format = pathlib.Path(
        "/sys/kernel/tracing/events/ras/aer_event/format")
    trace_event = pathlib.Path(
        "/sys/kernel/tracing/instances/rasdaemon/events/ras/aer_event/enable")
    if not trace_format.is_file():
        results.add("aer-native", "failed",
                    "guest kernel lacks ras:aer_event")
        return

    database = WORK_DIR / "ras-aer.db"
    database.unlink(missing_ok=True)
    aer_environment = dict(environment, RAS_SQLITE3_DATABASE=str(database))
    config = WORK_DIR / "rasdaemon-aer.env"
    config.write_text("".join("%s=%s\n" % item
                              for item in sorted(aer_environment.items())
                              if item[0] != "PYTHONDONTWRITEBYTECODE"),
                      encoding="utf-8")
    log_path = os.path.join(WORK_DIR, "aer-startup.log")
    log = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [str(build / "rasdaemon"), "--foreground", "--record",
         "--config", str(config)],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **aer_environment), start_new_session=True,
    )
    started = time.monotonic()
    try:
        events = [os.fspath(trace_event)]
        wait_daemon_ready(process, log_path, events)
        if process.poll() is not None:
            results.add("aer-native", "failed",
                        "rasdaemon exited before AER injection",
                        {"output": read_text(log_path)[-32768:]})
            return
        try:
            trace_enabled = trace_event.read_text(encoding="ascii").strip()
        except OSError as error:
            results.add("aer-native", "failed",
                        "rasdaemon tracefs instance was not created",
                        {"error": str(error), "path": str(trace_event)})
            return
        if trace_enabled != "1":
            results.add("aer-native", "failed",
                        "rasdaemon did not enable ras:aer_event",
                        {"trace_enabled": trace_enabled})
            return
        results.progress("aer-ready device=ras-aer error=BAD_DLLP")
        wait_for_rows(os.fspath(database), "aer_event", 20)
        os.killpg(process.pid, signal.SIGTERM)
        _output, _unused = process.communicate(timeout=30)
        output = read_text(log_path)
        query = run(["sqlite3", str(database),
                     "SELECT count(*) FROM aer_event;"])
        try:
            count = int(query.stdout.strip()) if query.returncode == 0 else 0
        except ValueError:
            count = 0
        evidence = {
            "device": "ras-aer",
            "error_type": "BAD_DLLP",
            "trace_enabled_before_injection": trace_enabled,
            "sqlite_count": count,
            "sqlite_output": query.stdout,
            "rasdaemon_output": output[-32768:],
            "rasdaemon_returncode": process.returncode,
        }
        status = "passed" if process.returncode == 0 and count > 0 else "failed"
        reason = "" if status == "passed" else \
            "QEMU AER did not produce a clean rasdaemon aer_event row"
        results.add("aer-native", status, reason, evidence,
                    time.monotonic() - started)
        if status == "passed":
            ras_mc_ctl = shutil.which("ras-mc-ctl") or "/usr/sbin/ras-mc-ctl"
            results.command(
                "aer-database-report",
                [ras_mc_ctl, "database", "--errors",
                 "--table", "aer_event", "--json"],
                environment=aer_environment,
            )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        results.add("aer-native", "failed", str(error))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

        log.close()


def block_error_smoke(results, build, environment):
    """Trigger a real guest block EIO through QEMU's blkdebug backend."""
    trace_root = pathlib.Path("/sys/kernel/tracing/events/block")
    candidates = [name for name in ("block_rq_error", "block_rq_complete")
                  if (trace_root / name / "format").is_file()]
    if not candidates:
        results.add("block-io-native", "failed",
                    "guest kernel lacks a supported block error tracepoint")
        return
    device = pathlib.Path("/dev/disk/by-id/virtio-ras-block-error")
    deadline = time.monotonic() + 10
    while not device.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not device.exists():
        results.add("block-io-native", "failed",
                    "blkdebug guest disk was not discovered")
        return

    database = WORK_DIR / "ras-block.db"
    database.unlink(missing_ok=True)
    block_environment = dict(environment, RAS_SQLITE3_DATABASE=str(database))
    config = WORK_DIR / "rasdaemon-block.env"
    config.write_text("".join("%s=%s\n" % item
                              for item in sorted(block_environment.items())
                              if item[0] != "PYTHONDONTWRITEBYTECODE"),
                      encoding="utf-8")
    log_path = os.path.join(WORK_DIR, "block-startup.log")
    log = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [str(build / "rasdaemon"), "--foreground", "--record",
         "--config", str(config)], stdout=log,
        stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **block_environment), start_new_session=True)
    started = time.monotonic()
    try:
        events = [os.path.join("/sys/kernel/tracing/instances/rasdaemon/events/block",
                                         candidate, "enable") for candidate in candidates]
        wait_daemon_ready(process, log_path, events)
        instance_root = pathlib.Path(
            "/sys/kernel/tracing/instances/rasdaemon/events/block")
        enabled_states = {}
        for candidate in candidates:
            try:
                enabled_states[candidate] = (instance_root / candidate /
                                             "enable").read_text(
                                                 encoding="ascii").strip()
            except OSError as error:
                enabled_states[candidate] = str(error)
        event = next((name for name in candidates
                      if enabled_states.get(name) == "1"), None)
        if process.poll() is not None or event is None:
            results.add("block-io-native", "failed",
                        "rasdaemon did not enable the block tracepoint",
                        {"trace_enabled": enabled_states})
            return
        enabled = enabled_states[event]
        write = run(["dd", "if=/dev/zero", "of=%s" % device,
                     "bs=4096", "count=1", "oflag=direct", "conv=fsync"])
        wait_for_rows(os.fspath(database), "disk_errors", 20)
        os.killpg(process.pid, signal.SIGTERM)
        _output, _unused = process.communicate(timeout=30)
        output = read_text(log_path)
        query = run(["sqlite3", str(database),
                     "SELECT count(*) FROM disk_errors;"])
        count = int(query.stdout.strip()) if query.returncode == 0 else 0
        evidence = {"device": str(device), "trace_event": event,
                    "trace_enabled_before_injection": enabled,
                    "dd_returncode": write.returncode,
                    "dd_output": write.stdout, "sqlite_count": count,
                    "sqlite_output": query.stdout,
                    "rasdaemon_output": output[-32768:]}
        passed = write.returncode != 0 and process.returncode == 0 and count > 0
        results.add("block-io-native", "passed" if passed else "failed",
                    "" if passed else
                    "blkdebug EIO did not produce a disk_errors row",
                    evidence, time.monotonic() - started)
        if passed:
            ras_mc_ctl = shutil.which("ras-mc-ctl") or "/usr/sbin/ras-mc-ctl"
            results.command(
                "block-database-report",
                [ras_mc_ctl, "database", "--errors",
                 "--table", "disk_errors", "--json"],
                environment=block_environment)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        results.add("block-io-native", "failed", str(error))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

        log.close()


def matching_record(scenario: dict, row: dict, evidence: dict) -> bool:
    """Distinguish the injected record from empty-list and background events."""
    name = scenario["name"]

    if name == "fuzz":
        guid = bytes.fromhex("78563412341278569abc123456789abc")
        return row.get("sec_type") == guid and row.get("error") == bytes(range(32))

    if name == "cxl-overflow":
        return row.get("count", 0) > 0

    if name == "memory-sigbus":
        return (row.get("sig") == signal.SIGBUS and row.get("code", 0) in (4, 5)
                and row.get("pid") == evidence["holder_pid"])

    expected = {
        "cxl-poison": {"dpa": 4096, "dpa_length": 64},
        "cxl-module": {"device_temp": 40, "life_used": 10},
        "cxl-aer-ce": {"error_status": 2},
        "cxl-aer-ue": {"error_status": 16},
    }

    if name in ("cxl-media", "cxl-dram"):
        expected[name] = {"dpa": scenario["arguments"]["dpa"]}

    if name == "memory-failure":
        expected[name] = {"pfn": f"{evidence['pfn']:#x}"}

    return all(row.get(key) == value for key, value in expected.get(name, {}).items())


def read_text(filename: str) -> str:
    """Read a guest text interface or retained log."""
    with open(filename, encoding="utf-8", errors="replace") as stream:
        return stream.read()


class ScenarioUnavailable(RuntimeError):
    """A test prerequisite is missing; other scenarios must still run."""


def wait_daemon_ready(process: subprocess.Popen, log_path: str,
                      events: list[str], timeout: float = 15) -> None:
    """Wait for this process's database and consumer, not stale trace flags."""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("rasdaemon exited during startup: " + read_text(log_path)[-4096:])

        output = read_text(log_path)
        database_ready = "Database backend started: sqlite3." in output
        listening = ("Listening to events for cpus " in output or
                     "Listening to events on cpu " in output)
        enabled = any(os.path.isfile(event) and read_text(event).strip() == "1"
                      for event in events)

        if database_ready and listening and enabled:
            return

        time.sleep(0.2)

    raise RuntimeError("rasdaemon startup timed out: " + read_text(log_path)[-4096:])


class RecordedScenario:
    """Own one daemon, injection target and database evidence check."""

    def __init__(self, results: Results, build: str, environment: dict[str, str],
                 scenario: dict) -> None:
        self.results = results
        self.scenario = scenario
        name = scenario["name"]
        self.paths = {
            "database": os.path.join(WORK_DIR, name + ".db"),
            "config": os.path.join(WORK_DIR, name + ".env"),
            "log": os.path.join(WORK_DIR, name + ".log"),
            "binary": os.path.join(build, "rasdaemon"),
            "enabled": os.path.join("/sys/kernel/tracing/instances/rasdaemon/events",
                                    scenario["event"], "enable"),
            "observer": os.path.join("/sys/kernel/tracing/instances", "ras-test-" + name),
        }
        self.environment = dict(environment, RAS_SQLITE3_DATABASE=self.paths["database"])
        self.evidence: dict = {"event": scenario["event"], "table": scenario["table"], "rows": []}
        self.process: subprocess.Popen | None = None

    def capture_cxl_aer_state(self, phase: str) -> None:
        """Retain PCI bindings and AER registers around CXL AER injection."""
        if not self.scenario["name"].startswith("cxl-aer-"):
            return

        devices = []

        for memdev in glob.glob("/sys/bus/cxl/devices/mem*"):
            target = os.path.realpath(memdev)
            components = target.split(os.sep)
            bdfs = [item for item in components
                    if len(item.split(":")) == 3 and "." in item]

            for bdf in bdfs[-2:]:
                device = os.path.join("/sys/bus/pci/devices", bdf)
                driver = os.path.join(device, "driver")
                binding = ""

                if os.path.islink(driver):
                    binding = os.path.basename(os.path.realpath(driver))
                completed = subprocess.run(
                    ["lspci", "-s", bdf, "-vv"], check=False, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
                )
                devices.append({
                    "bdf": bdf,
                    "driver": binding,
                    "lspci_returncode": completed.returncode,
                    "lspci": completed.stdout[-16384:],
                })

        self.evidence["cxl_aer_" + phase] = devices

    def unmask_cxl_aer_error(self) -> None:
        """Enable the component error selected by this CXL AER scenario."""
        registers = {
            "cxl-aer-ce": (0x1090, 1),
            "cxl-aer-ue": (0x1084, 4),
        }
        selected = registers.get(self.scenario["name"])

        if selected is None:
            return

        memory_devices = glob.glob("/sys/bus/cxl/devices/mem*")

        if len(memory_devices) != 1:
            raise RuntimeError("CXL AER setup requires exactly one memory device")

        bdfs = [item for item in os.path.realpath(memory_devices[0]).split(os.sep)
                if len(item.split(":")) == 3 and "." in item]

        if not bdfs:
            raise RuntimeError("CXL AER endpoint BDF was not found")

        offset, bit = selected
        resource = os.path.join("/sys/bus/pci/devices", bdfs[-1], "resource0")
        size = os.path.getsize(resource)

        with open(resource, "r+b", buffering=0) as stream:
            with mmap.mmap(stream.fileno(), size, flags=mmap.MAP_SHARED,
                           prot=mmap.PROT_READ | mmap.PROT_WRITE) as component:
                before = struct.unpack_from("<I", component, offset)[0]
                after = before & ~(1 << bit)
                struct.pack_into("<I", component, offset, after)
                component.flush(offset & ~(mmap.PAGESIZE - 1), mmap.PAGESIZE)
                readback = struct.unpack_from("<I", component, offset)[0]

        self.evidence["cxl_aer_component_mask"] = {
            "bdf": bdfs[-1],
            "resource": "resource0",
            "offset": offset,
            "bit": bit,
            "before": before,
            "after": readback,
        }

        if readback & (1 << bit):
            raise RuntimeError("CXL component error mask did not clear")

    def unmask_cxl_pcie_error(self) -> None:
        """Enable the PCIe internal error carrying this CXL notification."""
        registers = {"cxl-aer-ce": ("ECAP_AER+14.L", 1 << 14),
                     "cxl-aer-ue": ("ECAP_AER+08.L", 1 << 22)}
        selected = registers.get(self.scenario["name"])

        if selected is None:
            return

        # Some kernels provide CXL callbacks without unmasking the PCIe
        # internal errors that invoke them. Component masks alone cannot
        # enable notification. Keep this fixture workaround observable.
        register, mask = selected
        bdf = self.evidence["cxl_aer_component_mask"]["bdf"]
        command = ["setpci", "-s", bdf]
        before = run(command + [register])

        if before.returncode:
            raise RuntimeError("cannot read CXL PCIe AER mask: " + before.stdout)

        write = run(command + [f"{register}=00000000:{mask:08x}"])
        after = run(command + [register])

        if write.returncode or after.returncode:
            raise RuntimeError("cannot unmask CXL PCIe AER: " + write.stdout + after.stdout)

        try:
            values = {"before": int(before.stdout.strip(), 16),
                      "after": int(after.stdout.strip(), 16)}
        except ValueError as error:
            raise RuntimeError("invalid CXL PCIe AER mask readback") from error

        self.evidence["cxl_aer_pcie_mask"] = {
            "bdf": bdf, "register": register, "mask": mask, **values}

        if values["after"] != values["before"] & ~mask:
            raise RuntimeError("CXL PCIe AER mask did not clear only the selected bit")

    def start(self, log: IO[str]) -> None:
        """Start recording and wait for the required tracepoint."""
        event = self.scenario["event"]
        trace_format = os.path.join("/sys/kernel/tracing/events", event, "format")

        if not os.path.isfile(trace_format):
            raise ScenarioUnavailable("guest kernel lacks " + event)

        os.mkdir(self.paths["observer"])
        observer_event = os.path.join(self.paths["observer"], "events", event, "enable")

        with open(observer_event, "w", encoding="ascii") as stream:
            stream.write("1\n")

        settings = "".join(f"{key}={value}\n" for key, value in self.environment.items()
                           if key != "PYTHONDONTWRITEBYTECODE")

        with open(self.paths["config"], "w", encoding="utf-8") as stream:
            stream.write(settings)

        if self.scenario["name"].startswith("cxl-"):
            run(["modprobe", "cxl_pci"])
            run(["modprobe", "cxl_acpi"])

            if not glob.glob("/sys/bus/cxl/devices/mem*"):
                raise ScenarioUnavailable("QEMU CXL memory device did not bind to the guest driver")

            self.unmask_cxl_aer_error()
            self.unmask_cxl_pcie_error()
            self.capture_cxl_aer_state("before")

        command = [self.paths["binary"], "--foreground", "--record",
                   "--config", self.paths["config"]]
        self.process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                        env=dict(os.environ, **self.environment),
                                        start_new_session=True)
        wait_daemon_ready(self.process, self.paths["log"], [self.paths["enabled"]])
        self.evidence["trace_enabled_before_injection"] = "1"

    def ready(self) -> None:
        """Publish guest-discovered addresses before allowing host injection."""
        marker = self.scenario["name"] + "-ready"

        if self.scenario["name"] == "ghes-aer":
            endpoints = []

            for device in glob.glob("/sys/bus/pci/devices/*"):
                device_id = read_text(os.path.join(device, "device")).strip()
                vendor_id = read_text(os.path.join(device, "vendor")).strip()

                if device_id == "0x1044" and vendor_id == "0x1af4":
                    endpoints.append(os.path.basename(device))

            if len(endpoints) != 1:
                raise RuntimeError("dedicated virtio RNG AER endpoint was not found")

            self.evidence["bdf"] = endpoints[0]
            marker += " bdf=" + endpoints[0]

        self.results.progress(marker)
        self.evidence["injection_ready"] = True

    def inject_page(self) -> None:
        """Poison only a disposable helper's page inside this guest."""
        if self.scenario.get("producer") != "hwpoison":
            return

        if run(["modprobe", "hwpoison_inject"]).returncode:
            raise ScenarioUnavailable("hwpoison_inject module is unavailable")

        consume = self.scenario["name"] == "memory-sigbus"
        holder, physical, page_size = guest_test_page(consume)
        self.evidence["pfn"] = physical // page_size
        self.evidence["holder_pid"] = holder

        with open("/sys/kernel/debug/hwpoison/corrupt-pfn", "w", encoding="ascii") as stream:
            stream.write(f"{physical // page_size:#x}\n")

        if consume:
            with contextlib.suppress(ProcessLookupError):
                os.kill(holder, signal.SIGUSR1)

    def records(self) -> list[dict]:
        """Read committed rows without creating a missing database."""
        database = self.paths["database"]

        if not os.path.isfile(database):
            return []

        try:
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

            with contextlib.closing(connection):
                connection.row_factory = sqlite3.Row
                table = self.scenario["table"]
                rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()

            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []  # schema creation/commit may still be in progress

    def wait_record(self) -> None:
        """Wait for matching data, never accepting an empty poison-list trace."""
        timeout = 3300 if self.scenario["name"] == "fuzz" else 30
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("rasdaemon exited while recording")

            if self.scenario["name"] == "cxl-poison":
                triggers = glob.glob("/sys/bus/cxl/devices/mem*/trigger_poison_list")

                if not triggers:
                    raise ScenarioUnavailable("CXL poison-list sysfs trigger is unavailable")

                for trigger in triggers:
                    with open(trigger, "w", encoding="ascii") as stream:
                        stream.write("1\n")

            rows = self.records()

            if any(matching_record(self.scenario, row, self.evidence) for row in rows):
                self.evidence["rows"] = [
                    {key: value.hex() if isinstance(value, bytes) else value
                     for key, value in row.items()} for row in rows
                ]
                return

            time.sleep(0.25)

        raise RuntimeError("injection produced no matching " + self.scenario["table"] + " row")

    def cleanup(self) -> None:
        """Reap only the daemon and page helper owned by this scenario."""
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            self.capture_cxl_aer_state("after")

        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)

        holder = self.evidence.get("holder_pid")

        if holder is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(holder, signal.SIGKILL)

            with contextlib.suppress(ChildProcessError):
                os.waitpid(holder, 0)

        if os.path.isfile(self.paths["log"]):
            self.evidence["rasdaemon_output"] = read_text(self.paths["log"])[-32768:]

        observer = self.paths["observer"]

        if os.path.isdir(observer):
            with contextlib.suppress(OSError):
                trace = read_text(os.path.join(observer, "trace"))
                self.evidence["kernel_trace"] = trace[-65536:]
                event_name = self.scenario["event"].split("/")[-1] + ":"
                self.evidence["kernel_observed"] = event_name in trace
                enabled = os.path.join(observer, "events", self.scenario["event"], "enable")

                with open(enabled, "w", encoding="ascii") as stream:
                    stream.write("0\n")

                os.rmdir(observer)

    def execute(self) -> None:
        """Require recording, matching data, clean shutdown and a CLI report."""
        started = time.monotonic()
        name = self.scenario["name"]
        status, reason = "passed", ""

        try:
            with open(self.paths["log"], "w", encoding="utf-8") as log:
                self.start(log)
                self.ready()
                self.inject_page()
                self.wait_record()
                os.killpg(self.process.pid, signal.SIGTERM)
                self.process.wait(timeout=15)

                if self.process.returncode:
                    raise RuntimeError("rasdaemon did not shut down cleanly")

            self.evidence["sqlite_count"] = len(self.evidence["rows"])
        except ScenarioUnavailable as error:
            status, reason = "skipped", str(error)
        except (OSError, RuntimeError, sqlite3.Error, subprocess.TimeoutExpired) as error:
            status, reason = "failed", str(error)
        finally:
            self.cleanup()

        observed = self.evidence.get("kernel_observed") or bool(self.evidence["rows"])
        kernel = "PASS" if observed else "SKIP"
        daemon = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[status]

        if (status == "failed" and not observed and
                reason.startswith("injection produced no matching")):
            kernel, daemon = "FAIL", "SKIP"
            reason = "injection produced no kernel event or matching database row"

        if status == "skipped":
            kernel, daemon = "SKIP", "SKIP"

        self.results.add(name, status, reason, self.evidence, time.monotonic() - started,
                         kernel=kernel, rasdaemon=daemon)

        if status == "passed":
            command = ["/usr/sbin/ras-mc-ctl", "database", "--errors",
                       "--table", self.scenario["table"], "--json"]
            self.results.command(name + "-report", command, environment=self.environment)


def daemon_smoke(results, build, environment):
    """Verify trace discovery, recording setup, and normal signal shutdown."""
    binary = build / "rasdaemon"
    database = pathlib.Path(environment["RAS_SQLITE3_DATABASE"])
    database.parent.mkdir(parents=True, exist_ok=True)
    config = WORK_DIR / "rasdaemon.env"
    config.write_text("".join("%s=%s\n" % item
                              for item in sorted(environment.items())
                              if item[0] != "PYTHONDONTWRITEBYTECODE"),
                      encoding="utf-8")
    started = time.monotonic()
    process = subprocess.Popen(
        [str(binary), "--foreground", "--record", "--config", str(config)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **environment), start_new_session=True,
    )
    try:
        time.sleep(3)
        if process.poll() is not None:
            output = process.stdout.read()
            reason = "daemon exited during startup"
            if "no events were enabled" in output.lower():
                results.add("daemon-lifecycle", "skipped", reason,
                            {"output": output})
                return
            results.add("daemon-lifecycle", "failed", reason,
                        {"output": output})
            return
        os.killpg(process.pid, signal.SIGTERM)
        output, _unused = process.communicate(timeout=30)
        status = "passed" if process.returncode == 0 else "failed"
        results.add("daemon-lifecycle", status,
                    "" if status == "passed" else "unclean shutdown",
                    {"returncode": process.returncode, "output": output[-32768:]},
                    time.monotonic() - started)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)

    if database.is_file():
        results.command(
            "sqlite-schema",
            ["sqlite3", str(database),
             "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"],
        )
    else:
        results.add("sqlite-schema", "failed", "database was not created")


def installed_interface_smoke(results, environment):
    """Exercise the installed service and accepted Python CLI interfaces."""
    results.command("rasdaemon-cli", ["/usr/sbin/rasdaemon", "--help"])
    results.command(
        "dimm-status", ["/usr/sbin/ras-mc-ctl", "dimm", "--status"],
        environment=environment, expected_returncodes=(0, 1),
    )
    results.command(
        "database-json",
        ["/usr/sbin/ras-mc-ctl", "db", "--list-tables", "--json"],
        environment=environment,
    )

    config = pathlib.Path("/etc/sysconfig/rasdaemon")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("".join("%s=%s\n" % item
                              for item in sorted(environment.items())
                              if item[0] != "PYTHONDONTWRITEBYTECODE"),
                      encoding="utf-8")
    results.command("systemd-daemon-reload", ["systemctl", "daemon-reload"])
    verified = results.command(
        "systemd-unit-verify",
        ["systemd-analyze", "verify", "rasdaemon.service"], required=False,
    )
    if not verified:
        return
    started = results.command(
        "systemd-service-start", ["systemctl", "start", "rasdaemon.service"],
        timeout=60, required=False,
    )
    if started:
        results.command("systemd-service-stop",
                        ["systemctl", "stop", "rasdaemon.service"], timeout=60)


def execute(profile):
    """Execute the selected test profile."""
    results = Results()
    try:
        payload = mount_payload()
    except RuntimeError as error:
        results.add("payload", "failed", str(error))
        return results
    try:
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR)
        WORK_DIR.mkdir(parents=True)
        results.add("payload", "passed")

        if profile not in ("baseline", "injection", "fuzz"):
            results.add(profile, "skipped",
                        "%s profile requires an optional guest artifact" % profile)
            return results

        archive = payload / "rasdaemon-install.tar"
        if archive.is_file():
            if not results.command(
                    "install-payload", ["tar", "-xf", str(archive), "-C", "/"]):
                return results
            build = pathlib.Path("/usr/sbin")
        else:
            source = WORK_DIR / "source"
            # Compatibility fallback for locally built legacy guest images.
            shutil.copytree(payload, source, copy_function=shutil.copy)
            build = source / "build-qemu"
            configured = results.command(
                "configure", ["meson", "setup", str(build),
                               "-Dsqlite3=enabled", "-Dmysql=disabled",
                               "-Dpostgresql=disabled",
                               "-Dpcie-edpc=disabled"], cwd=source,
            )
            if not configured:
                return results
            if not results.command("build", ["ninja", "-C", str(build)],
                                   cwd=source):
                return results
            if not results.command(
                    "install", ["meson", "install", "-C", str(build)],
                    cwd=source):
                return results
        if not ensure_tracefs(results):
            return results
        environment = safe_environment(WORK_DIR / "ras-mce.db")
        if profile == "fuzz":
            scenario = {"name": "fuzz", "event": "ras/non_standard_event",
                        "table": "non_standard_event"}
            RecordedScenario(results, os.fspath(build), environment, scenario).execute()
        elif profile == "injection":
            debugfs = "/sys/kernel/debug"
            os.makedirs(debugfs, exist_ok=True)

            if run(["mountpoint", "-q", debugfs]).returncode:
                command = ["mount", "-t", "debugfs", "debugfs", debugfs]

                if not results.command("debugfs", command):
                    return results

            mce_memory_smoke(results, build, environment)
            if os.uname().machine == "x86_64":
                aer_smoke(results, build, environment)
            block_error_smoke(results, build, environment)
            scenarios = json.loads(read_text(os.path.join(payload, "scenarios.json")))

            for scenario in scenarios:
                RecordedScenario(results, os.fspath(build), environment, scenario).execute()
        else:
            daemon_smoke(results, build, environment)
            installed_interface_smoke(results, environment)
    finally:
        run(["umount", str(payload)])
        with contextlib.suppress(OSError):
            payload.rmdir()
    return results


class GuestHeartbeat:
    """Report userspace scheduling independently of the current test command."""

    def __init__(self) -> None:
        self.finished = threading.Event()
        self.thread = threading.Thread(target=self.report, daemon=True)

    def report(self) -> None:
        """A live heartbeat with an unchanged phase can expose an agent stall."""
        while not self.finished.wait(10):
            print(f"ras-qemu-agent: heartbeat phase={Results.phase}", flush=True)

    def __enter__(self) -> "GuestHeartbeat":
        self.thread.start()
        return self

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        self.finished.set()
        self.thread.join(timeout=1)


def main():
    """Run once at boot and write exactly one JSON document."""
    profile = "baseline"
    payload = None
    try:
        payload = mount_payload()
        profile_file = payload / ".ras-qemu-profile"
        if profile_file.is_file():
            profile = profile_file.read_text(encoding="utf-8").strip()
    except RuntimeError:
        pass
    finally:
        if payload:
            run(["umount", str(payload)])
            with contextlib.suppress(OSError):
                payload.rmdir()
    results = execute(profile)
    document = results.document(profile)
    RESULT_PORT.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 60
    while not RESULT_PORT.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    with open(RESULT_PORT, "w", encoding="utf-8") as stream:
        json.dump(document, stream, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    with GuestHeartbeat():
        main()

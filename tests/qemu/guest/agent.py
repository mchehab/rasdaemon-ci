#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Mauro Carvalho Chehab <mchehab+huawei@kernel.org>

"""Build and exercise the payload supplied to a rasdaemon QEMU guest."""

import datetime
import contextlib
import ctypes
import json
import mmap
import os
import pathlib
import shutil
import signal
import struct
import subprocess
import tempfile
import time


RESULT_PORT = pathlib.Path(
    "/dev/virtio-ports/org.rasdaemon.test.0")
WORK_DIR = pathlib.Path("/var/tmp/rasdaemon-qemu")


class Results:
    """Collect scenario results while continuing after ordinary failures."""

    def __init__(self):
        self.tests = []

    def add(self, name, status, reason="", evidence=None, duration=0.0):
        self.tests.append({
            "name": name,
            "status": status,
            "reason": reason,
            "evidence": evidence or {},
            "duration_seconds": round(duration, 3),
        })

    @staticmethod
    def progress(message):
        """Write a short status line to the retained guest serial console."""
        try:
            with open("/dev/console", "w", encoding="utf-8") as stream:
                stream.write("ras-qemu-agent: %s\n" % message)
        except OSError:
            pass

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


def guest_test_page():
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
    holder = os.fork()
    if holder == 0:
        try:
            signal.pause()
        finally:
            os._exit(0)
    page.close()
    return holder, physical, page_size


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
    process = subprocess.Popen(
        [str(build / "rasdaemon"), "--foreground", "--record",
         "--config", str(config)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **environment), start_new_session=True,
    )
    page_holder = None
    started = time.monotonic()
    try:
        time.sleep(3)
        if process.poll() is not None:
            results.add("mce-hardware-first", "failed",
                        "rasdaemon exited before MCE injection",
                        {"output": process.stdout.read()[-32768:]})
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
        page_holder, physical, _page_size = guest_test_page()
        for interval in poll_intervals:
            interval.write_text("1\n", encoding="ascii")
        results.progress("mce-ready physical=%#x" % physical)
        # The host runner watches the retained console and injects through
        # QEMU's monitor only after this readiness marker is visible.
        time.sleep(15)
        os.killpg(process.pid, signal.SIGTERM)
        output, _unused = process.communicate(timeout=30)
        query = run(["sqlite3", str(database),
                     "SELECT count(*) FROM mce_record;"])
        try:
            count = int(query.stdout.strip()) if query.returncode == 0 else 0
        except ValueError:
            count = 0
        evidence = {
            "physical_address": "%#x" % physical,
            "poll_intervals": [str(path) for path in poll_intervals],
            "trace_enabled_before_injection": trace_enabled,
            "sqlite_count": count,
            "sqlite_output": query.stdout,
            "rasdaemon_output": output[-32768:],
            "rasdaemon_returncode": process.returncode,
        }
        status = "passed" if process.returncode == 0 and count > 0 else "failed"
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
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        results.add("mce-hardware-first", "failed", str(error))
    finally:
        if page_holder is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(page_holder, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(page_holder, 0)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def aer_smoke(results, build, environment):
    """Receive a QEMU PCIe AER error and verify real rasdaemon recording."""
    trace_format = pathlib.Path(
        "/sys/kernel/tracing/events/ras/aer_event/format")
    trace_event = pathlib.Path(
        "/sys/kernel/tracing/instances/rasdaemon/events/ras/aer_event/enable")
    if not trace_format.is_file():
        results.add("aer-native", "skipped",
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
    process = subprocess.Popen(
        [str(build / "rasdaemon"), "--foreground", "--record",
         "--config", str(config)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **aer_environment), start_new_session=True,
    )
    started = time.monotonic()
    try:
        time.sleep(3)
        if process.poll() is not None:
            results.add("aer-native", "failed",
                        "rasdaemon exited before AER injection",
                        {"output": process.stdout.read()[-32768:]})
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
        time.sleep(10)
        os.killpg(process.pid, signal.SIGTERM)
        output, _unused = process.communicate(timeout=30)
        query = run(["sqlite3", str(database),
                     "SELECT count(*) FROM aer_event;"])
        try:
            count = int(query.stdout.strip()) if query.returncode == 0 else 0
        except ValueError:
            count = 0
        evidence = {
            "device": "ras-aer",
            "error": "BAD_DLLP",
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
    except (OSError, subprocess.TimeoutExpired) as error:
        results.add("aer-native", "failed", str(error))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def block_error_smoke(results, build, environment):
    """Trigger a real guest block EIO through QEMU's blkdebug backend."""
    trace_root = pathlib.Path("/sys/kernel/tracing/events/block")
    candidates = [name for name in ("block_rq_error", "block_rq_complete")
                  if (trace_root / name / "format").is_file()]
    if not candidates:
        results.add("block-io-native", "skipped",
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
    process = subprocess.Popen(
        [str(build / "rasdaemon"), "--foreground", "--record",
         "--config", str(config)], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
        env=dict(os.environ, **block_environment), start_new_session=True)
    started = time.monotonic()
    try:
        time.sleep(3)
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
        time.sleep(3)
        os.killpg(process.pid, signal.SIGTERM)
        output, _unused = process.communicate(timeout=30)
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
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        results.add("block-io-native", "failed", str(error))
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


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

        if profile not in ("baseline", "injection"):
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
        if profile == "injection":
            mce_memory_smoke(results, build, environment)
            if os.uname().machine == "x86_64":
                aer_smoke(results, build, environment)
            block_error_smoke(results, build, environment)
        else:
            daemon_smoke(results, build, environment)
            installed_interface_smoke(results, environment)
    finally:
        run(["umount", str(payload)])
        with contextlib.suppress(OSError):
            payload.rmdir()
    return results


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
    main()

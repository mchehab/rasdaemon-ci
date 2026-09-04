#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Mauro Carvalho Chehab <mchehab+huawei@kernel.org>

"""Run rasdaemon regression tests in isolated QEMU guests."""

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET


FORMAT_VERSION = 1
PROJECT_DIR = pathlib.Path(__file__).resolve().parents[2]
HARNESS_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_MANIFEST = pathlib.Path(__file__).with_name("images.json")
RESULT_PORT_NAME = "org.rasdaemon.test.0"
ANSI_ESCAPE = re.compile(
    r"\x1b(?:"
    r"\][^\x07]*(?:\x07|\x1b\\)|"       # OSC ... BEL/ST
    r"[PX^_].*?\x1b\\|"                  # DCS/SOS/PM/APC ... ST
    r"\[[0-?]*[ -/]*[@-~]|"              # CSI
    r"[@-_]"                              # two-byte escape
    r")",
    re.DOTALL,
)
NON_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class LabError(RuntimeError):
    """A deterministic test-lab failure."""


@dataclasses.dataclass
class Check:
    """One capability check."""

    name: str
    available: bool
    reason: str
    value: str = ""

    def as_dict(self):
        """Return a JSON-serializable representation."""
        return dataclasses.asdict(self)


def utc_now():
    """Return an RFC 3339 UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def architecture_name(value):
    """Normalize architecture aliases used by QEMU and uname."""
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
        "riscv": "riscv64",
    }
    return aliases.get(value.lower(), value.lower())


def copy_text_console(source, destination):
    """Copy a serial console as normalized text without terminal escapes."""
    text = source.read_text(encoding="utf-8", errors="replace")
    text = ANSI_ESCAPE.sub("", text)
    text = re.sub(r"\r+\n", "\n", text)
    text = text.replace("\r", "\n")
    text = NON_TEXT_CONTROL.sub("", text)
    destination.write_text(text, encoding="utf-8")


def image_env_name(arch):
    """Return the per-architecture image override variable."""
    return "RAS_QEMU_IMAGE_" + arch.upper().replace("-", "_")


def sha256_file(path):
    """Calculate a file SHA-256 digest without loading it into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path):
    """Load and minimally validate the image/architecture manifest."""
    with open(path, "r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("format_version") != FORMAT_VERSION:
        raise LabError("unsupported image manifest format")
    architectures = manifest.get("architectures")
    if not isinstance(architectures, dict) or not architectures:
        raise LabError("image manifest has no architectures")
    required = {"qemu_binary", "machine", "image", "memory_mb", "cpus"}
    for arch, descriptor in architectures.items():
        missing = sorted(required.difference(descriptor))
        if missing:
            raise LabError("%s descriptor misses: %s" %
                           (arch, ", ".join(missing)))
    return manifest


def command_version(binary):
    """Read the first version line from a QEMU-compatible command."""
    try:
        completed = subprocess.run(
            [binary, "--version"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return "", str(error)
    first = completed.stdout.splitlines()
    if completed.returncode or not first:
        return "", "version command failed"
    return first[0], ""


class CapabilityProbe:
    """Inspect host prerequisites without changing host state."""

    def __init__(self, manifest, cache_dir):
        self.manifest = manifest
        self.cache_dir = pathlib.Path(cache_dir).expanduser().resolve()

    def _image_path(self, arch, descriptor):
        override = os.environ.get(image_env_name(arch))
        if override:
            return (pathlib.Path(override).expanduser().resolve(),
                    "environment override")
        return self.cache_dir / descriptor["image"]["filename"], "manifest cache"

    def inspect(self, arch):
        """Return all prerequisite checks for one architecture."""
        descriptor = self.manifest["architectures"][arch]
        checks = []
        binary = shutil.which(descriptor["qemu_binary"])
        if binary:
            version, error = command_version(binary)
            checks.append(Check("qemu", not error, error or "available", version))
        else:
            checks.append(Check("qemu", False,
                                "%s not found" % descriptor["qemu_binary"]))

        qemu_img = shutil.which("qemu-img")
        checks.append(Check("qemu-img", bool(qemu_img),
                            "available" if qemu_img else "qemu-img not found",
                            qemu_img or ""))

        image, source = self._image_path(arch, descriptor)
        image_ok = image.is_file()
        image_reason = source if image_ok else "%s is missing" % image
        expected = descriptor["image"].get("sha256", "")
        if image_ok and expected:
            actual = sha256_file(image)
            image_ok = actual == expected
            if not image_ok:
                image_reason = "SHA-256 mismatch"
        checks.append(Check("image", image_ok, image_reason, str(image)))

        firmware = descriptor.get("firmware", {})
        pairs = firmware.get("pairs", [])
        if pairs:
            pair = next((candidate for candidate in pairs
                         if pathlib.Path(candidate["code"]).is_file() and
                         pathlib.Path(candidate["vars"]).is_file()), None)
            reason = "available" if pair else "no matching pair found"
            checks.append(Check("firmware-code", bool(pair), reason,
                                pair["code"] if pair else ""))
            checks.append(Check("firmware-vars", bool(pair), reason,
                                pair["vars"] if pair else ""))
        for key in ("code", "vars", "bios"):
            candidates = firmware.get(key, [])
            if not candidates:
                continue
            found = next((candidate for candidate in candidates
                          if pathlib.Path(candidate).is_file()), "")
            checks.append(Check("firmware-%s" % key, bool(found),
                                "available" if found else "no candidate found",
                                found))

        host_arch = architecture_name(platform.machine())
        kvm_ok = (host_arch == arch and os.path.exists("/dev/kvm") and
                  os.access("/dev/kvm", os.R_OK | os.W_OK))
        if host_arch != arch:
            kvm_reason = "host architecture is %s" % host_arch
        elif not os.path.exists("/dev/kvm"):
            kvm_reason = "/dev/kvm is absent"
        elif not kvm_ok:
            kvm_reason = "/dev/kvm is inaccessible"
        else:
            kvm_reason = "available"
        checks.append(Check("kvm", kvm_ok, kvm_reason, "/dev/kvm"))
        checks.append(Check("tcg", bool(binary),
                            "available" if binary else "QEMU is unavailable"))
        return checks


class QmpClient:
    """Small newline-framed QEMU Machine Protocol client."""

    def __init__(self, path, timeout=10):
        self.path = str(path)
        self.timeout = timeout
        self.sock = None
        self.stream = None
        self.transcript = []

    def connect(self):
        """Connect and negotiate QMP capabilities."""
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(self.timeout)
                self.sock.connect(self.path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if self.sock:
                    self.sock.close()
                if time.monotonic() >= deadline:
                    raise LabError("QMP socket did not become ready")
                time.sleep(0.1)
        self.stream = self.sock.makefile("rwb", buffering=0)
        greeting = self._receive()
        if "QMP" not in greeting:
            raise LabError("invalid QMP greeting")
        self.execute("qmp_capabilities")
        return self

    def close(self):
        """Close the QMP stream and socket."""
        if self.stream:
            self.stream.close()
            self.stream = None
        if self.sock:
            self.sock.close()
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, _type, _value, _traceback):
        self.close()

    def _receive(self):
        while True:
            line = self.stream.readline()
            if not line:
                raise LabError("QMP closed the connection")
            message = json.loads(line.decode("utf-8"))
            self.transcript.append({"direction": "receive", "message": message})
            if "event" not in message:
                return message

    def execute(self, command, arguments=None):
        """Execute one QMP command and return its result."""
        request = {"execute": command}
        if arguments is not None:
            request["arguments"] = arguments
        self.transcript.append({"direction": "send", "message": request})
        self.stream.write((json.dumps(request) + "\r\n").encode("utf-8"))
        response = self._receive()
        if "error" in response:
            raise LabError("QMP %s failed: %s" % (command, response["error"]))
        return response.get("return")

    def has_command(self, name):
        """Return whether a command is advertised by this QEMU process."""
        commands = self.execute("query-commands")
        return any(command.get("name") == name for command in commands)


class ResultDocument:
    """Build JSON and JUnit representations of a lab run."""

    def __init__(self, arch, accelerator, profile):
        self.data = {
            "format_version": FORMAT_VERSION,
            "started_at": utc_now(),
            "architecture": arch,
            "accelerator": accelerator,
            "profile": profile,
            "host": {
                "architecture": architecture_name(platform.machine()),
                "kernel": platform.release(),
            },
            "capabilities": [],
            "tests": [],
        }

    def add_test(self, name, status, reason="", evidence=None, duration=0.0):
        """Append a pass, fail, or skip result."""
        self.data["tests"].append({
            "name": name,
            "status": status,
            "reason": reason,
            "duration_seconds": round(duration, 3),
            "evidence": evidence or {},
        })

    def finish(self):
        """Finalize totals and completion timestamp."""
        self.data["finished_at"] = utc_now()
        self.data["totals"] = {
            state: sum(test["status"] == state for test in self.data["tests"])
            for state in ("passed", "failed", "skipped")
        }

    def write(self, result_dir):
        """Write result.json and junit.xml."""
        self.finish()
        result_dir.mkdir(parents=True, exist_ok=True)
        json_path = result_dir / "result.json"
        with open(json_path, "w", encoding="utf-8") as stream:
            json.dump(self.data, stream, indent=2, sort_keys=True)
            stream.write("\n")

        suite = ET.Element("testsuite", {
            "name": "rasdaemon-qemu-%s" % self.data["architecture"],
            "tests": str(len(self.data["tests"])),
            "failures": str(self.data["totals"]["failed"]),
            "skipped": str(self.data["totals"]["skipped"]),
        })
        for result in self.data["tests"]:
            case = ET.SubElement(suite, "testcase", {
                "name": result["name"],
                "time": str(result["duration_seconds"]),
            })
            if result["status"] == "skipped":
                ET.SubElement(case, "skipped", {
                    "message": result["reason"],
                })
            elif result["status"] == "failed":
                failure = ET.SubElement(case, "failure", {
                    "message": result["reason"],
                })
                failure.text = json.dumps(result["evidence"], sort_keys=True)
        ET.ElementTree(suite).write(
            result_dir / "junit.xml", encoding="utf-8", xml_declaration=True,
        )
        return json_path


class VirtualMachine:
    """Own one QEMU process and all of its temporary resources."""

    def __init__(self, descriptor, arch, accelerator, image, work_dir,
                 source_dir, timeout, profile="baseline"):
        self.descriptor = descriptor
        self.arch = arch
        self.accelerator = accelerator
        self.image = pathlib.Path(image)
        self.work_dir = pathlib.Path(work_dir)
        self.source_dir = pathlib.Path(source_dir)
        self.timeout = timeout
        self.profile = profile
        self.process = None
        self.qmp_path = self.work_dir / "qmp.sock"
        self.result_path = self.work_dir / "result.sock"
        self.console_path = self.work_dir / "console.log"
        self.qemu_log_path = self.work_dir / "qemu.log"
        self.qemu_log = None
        self.overlay_path = self.work_dir / "overlay.qcow2"
        self.payload_dir = self.work_dir / "payload"
        self.block_image = self.work_dir / "block-error.raw"
        self.blkdebug_config = self.work_dir / "blkdebug.conf"

    def _find_firmware(self, key):
        candidates = self.descriptor.get("firmware", {}).get(key, [])
        return next((candidate for candidate in candidates
                     if pathlib.Path(candidate).is_file()), "")

    def _find_firmware_pair(self):
        pairs = self.descriptor.get("firmware", {}).get("pairs", [])
        return next((pair for pair in pairs
                     if pathlib.Path(pair["code"]).is_file() and
                     pathlib.Path(pair["vars"]).is_file()), None)

    def prepare(self):
        """Create overlay and tracked-source payload."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2",
            "-b", str(self.image), str(self.overlay_path),
        ]
        completed = subprocess.run(command, check=False, text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        if completed.returncode:
            raise LabError("qemu-img failed: %s" % completed.stdout.strip())
        self._stage_source()
        if self.profile == "injection":
            with open(self.block_image, "wb") as stream:
                stream.truncate(16 * 1024 * 1024)
            self.blkdebug_config.write_text(
                '[inject-error]\n'
                'event = "write_aio"\n'
                'errno = "5"\n', encoding="ascii")

    def _stage_source(self):
        """Build rasdaemon on the host and stage only installed output."""
        self.payload_dir.mkdir()
        build = self.work_dir / "host-build"
        install = self.work_dir / "host-install"
        commands = [
            (["meson", "setup", str(build), str(self.source_dir),
              "--prefix=/usr", "-Dsqlite3=enabled", "-Dmysql=disabled",
              "-Dpostgresql=disabled", "-Dpcie-edpc=disabled"], None),
            (["ninja", "-C", str(build)], None),
            (["meson", "install", "-C", str(build),
              "--destdir", str(install)], None),
        ]
        for command, environment in commands:
            completed = subprocess.run(
                command, check=False, text=True, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            if completed.returncode:
                raise LabError("host payload build failed: %s\n%s" %
                               (" ".join(command), completed.stdout[-32768:]))
        archive = self.payload_dir / "rasdaemon-install.tar"
        with tarfile.open(archive, "w") as stream:
            for entry in install.iterdir():
                stream.add(entry, arcname=entry.name, recursive=True)
        agent = HARNESS_DIR / "guest/agent.py"
        if not agent.is_file():
            raise LabError("guest agent is missing from the CI harness")
        shutil.copy2(agent, self.payload_dir / "agent.py")
        (self.payload_dir / ".ras-qemu-profile").write_text(
            self.profile + "\n", encoding="utf-8")

    def command(self):
        """Construct the inspectable direct-QEMU command line."""
        binary = shutil.which(self.descriptor["qemu_binary"])
        if not binary:
            raise LabError("%s not found" % self.descriptor["qemu_binary"])
        command = [
            binary,
            "-name", "rasdaemon-test-%s" % self.arch,
            "-machine", self.descriptor["machine"],
            "-accel", self.accelerator,
            "-m", str(self.descriptor["memory_mb"]),
            "-smp", str(self.descriptor["cpus"]),
            "-display", "none",
            "-no-reboot",
            "-qmp", "unix:%s,server=on,wait=off" % self.qmp_path,
            "-serial", "file:%s" % self.console_path,
            "-drive", "file=%s,if=virtio,format=qcow2" % self.overlay_path,
            "-drive", "file=fat:ro:%s,if=virtio,format=raw,readonly=on" %
            self.payload_dir,
            "-device", "virtio-serial",
            "-chardev", "socket,id=ras-result,path=%s,server=on,wait=off" %
                        self.result_path,
            "-device", "virtserialport,chardev=ras-result,name=%s" %
                       RESULT_PORT_NAME,
        ]
        cpu = self.descriptor.get("cpu", {}).get(self.accelerator)
        if cpu:
            command.extend(["-cpu", cpu])
        pair = self._find_firmware_pair()
        code = pair["code"] if pair else self._find_firmware("code")
        vars_template = pair["vars"] if pair else self._find_firmware("vars")
        bios = self._find_firmware("bios")
        if code:
            command.extend([
                "-drive", "if=pflash,format=raw,readonly=on,file=%s" % code,
            ])
        if vars_template:
            vars_copy = self.work_dir / "firmware-vars.fd"
            if not vars_copy.exists():
                shutil.copy2(vars_template, vars_copy)
            command.extend([
                "-drive", "if=pflash,format=raw,file=%s" % vars_copy,
            ])
        if bios:
            command.extend(["-bios", bios])
        if self.profile == "injection" and self.arch == "x86_64":
            command.extend([
                "-device", "pcie-root-port,id=ras-aer-root,slot=4",
                "-device",
                "virtio-rng-pci,id=ras-aer,bus=ras-aer-root,aer=on",
            ])
        if self.profile == "injection":
            command.extend([
                "-drive",
                "if=none,format=raw,cache=none,id=ras-block-drive,file="
                "blkdebug:%s:%s" % (self.blkdebug_config, self.block_image),
                "-device",
                "virtio-blk-pci,id=ras-block-error,drive=ras-block-drive,"
                "serial=ras-block-error",
            ])
        return command

    def _ready_markers(self):
        """Return scenario readiness markers found in the guest console."""
        try:
            console = self.console_path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return {}
        markers = {}
        for match in re.finditer(
                r"ras-qemu-agent: ([a-z0-9-]+)-ready(?: ([^\r\n]*))?",
                console):
            values = {}
            for key, value in re.findall(r"([a-z_]+)=([^ ]+)",
                                         match.group(2) or ""):
                values[key] = value
            markers[match.group(1)] = values
        return markers

    def _mce_ready_address(self):
        """Return the guest-owned physical address from the console marker."""
        value = self._ready_markers().get("mce", {}).get("physical")
        return int(value, 0) if value else None

    def _inject_mce(self, physical):
        """Inject a corrected hardware-first memory MCE through HMP/QMP."""
        # VAL|EN|MISCV|ADDRV with memory-controller MCACOD 0x90.  With UC
        # clear QEMU updates the bank without raising a synchronous #MC; the
        # guest's shortened polling interval discovers and reports the event.
        status = 0x9c00000000000090
        command_line = "mce 0 0 %#x 0 %#x 0" % (status, physical)
        with QmpClient(self.qmp_path, timeout=30) as qmp:
            if not qmp.has_command("human-monitor-command"):
                raise LabError("QEMU lacks human-monitor-command for MCE")
            response = qmp.execute(
                "human-monitor-command", {"command-line": command_line})
            transcript = qmp.transcript
        output = response if isinstance(response, str) else str(response)
        failures = ("not supported", "invalid", "disabled", "triple fault")
        if any(failure in output.lower() for failure in failures):
            raise LabError("QEMU MCE injection failed: %s" % output.strip())
        return {
            "command": command_line,
            "physical_address": "%#x" % physical,
            "status": "%#x" % status,
            "response": response,
            "transcript": transcript,
        }

    def _inject_aer(self):
        """Inject a correctable error into the dedicated PCIe endpoint."""
        command_line = "pcie_aer_inject_error ras-aer BAD_DLLP"
        with QmpClient(self.qmp_path, timeout=30) as qmp:
            if not qmp.has_command("human-monitor-command"):
                raise LabError("QEMU lacks human-monitor-command for AER")
            response = qmp.execute(
                "human-monitor-command", {"command-line": command_line})
            transcript = qmp.transcript
        output = response if isinstance(response, str) else str(response)
        if "OK id: ras-aer" not in output:
            raise LabError("QEMU AER injection failed: %s" % output.strip())
        return {
            "command": command_line,
            "device": "ras-aer",
            "error": "BAD_DLLP",
            "response": response,
            "transcript": transcript,
        }

    def run(self):
        """Run until the guest emits one JSON document or the timeout expires."""
        command = self.command()
        self.qemu_log = self.qemu_log_path.open("wb")
        self.process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=self.qemu_log,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        deadline = time.monotonic() + self.timeout
        payload = b""
        client = None
        mce_injected = False
        aer_injected = False
        qmp_evidence = {}
        try:
            with QmpClient(self.qmp_path, timeout=30) as qmp:
                qmp_evidence["version"] = qmp.execute("query-version")
                qmp_evidence["inject-ghes-v2-error"] = qmp.has_command(
                    "inject-ghes-v2-error")
                qmp_evidence["transcript"] = qmp.transcript
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise LabError("QEMU exited before returning guest results")
                try:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(1)
                    client.connect(str(self.result_path))
                    break
                except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
                    if client:
                        client.close()
                    client = None
                    time.sleep(0.2)
            if client is None:
                raise LabError("guest result channel did not become ready")
            while time.monotonic() < deadline:
                if self.profile == "injection" and self.arch == "x86_64" and \
                   not mce_injected:
                    physical = self._mce_ready_address()
                    if physical is not None:
                        qmp_evidence["mce"] = self._inject_mce(physical)
                        mce_injected = True
                if self.profile == "injection" and self.arch == "x86_64" and \
                   not aer_injected and "aer" in self._ready_markers():
                    qmp_evidence["aer"] = self._inject_aer()
                    aer_injected = True
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                payload += chunk
                if b"\n" in payload:
                    payload = payload.split(b"\n", 1)[0]
                    break
            if not payload:
                raise LabError("guest returned no result document")
            return json.loads(payload.decode("utf-8")), command, qmp_evidence
        finally:
            if client:
                client.close()
            self.stop()

    def stop(self):
        """Stop only the process group created by this instance."""
        if not self.process or self.process.poll() is not None:
            if self.qemu_log:
                self.qemu_log.close()
                self.qemu_log = None
            return
        os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)
        if self.qemu_log:
            self.qemu_log.close()
            self.qemu_log = None


def choose_accelerator(requested, checks):
    """Select KVM or TCG without silently relabeling fallback coverage."""
    available = {check.name: check for check in checks}
    if requested == "auto":
        return "kvm" if available["kvm"].available else "tcg"
    if not available[requested].available:
        raise LabError(available[requested].reason)
    return requested


def print_json(value):
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def run_probe(args, manifest):
    probe = CapabilityProbe(manifest, args.cache_dir)
    arches = manifest["architectures"] if args.arch == "all" else [args.arch]
    report = {
        "format_version": FORMAT_VERSION,
        "generated_at": utc_now(),
        "architectures": {
            arch: [check.as_dict() for check in probe.inspect(arch)]
            for arch in arches
        },
    }
    print_json(report)
    return 0


def run_test(args, manifest):
    descriptor = manifest["architectures"][args.arch]
    probe = CapabilityProbe(manifest, args.cache_dir)
    checks = probe.inspect(args.arch)
    document = ResultDocument(args.arch, args.accelerator, args.profile)
    document.data["capabilities"] = [check.as_dict() for check in checks]
    result_dir = pathlib.Path(args.result_dir).resolve()
    try:
        accelerator = choose_accelerator(args.accelerator, checks)
        document.data["accelerator"] = accelerator
    except LabError as error:
        document.add_test("prerequisites", "skipped", str(error))
        document.write(result_dir)
        return 0

    required = [check for check in checks
                if check.name in ("qemu", "qemu-img", "image") or
                check.name.startswith("firmware-")]
    missing = [check.reason for check in required if not check.available]
    if missing:
        document.add_test("prerequisites", "skipped", "; ".join(missing))
        document.write(result_dir)
        return 0
    document.add_test("prerequisites", "passed")

    image = next(check.value for check in checks if check.name == "image")
    work_parent = pathlib.Path(args.work_dir).expanduser() if args.work_dir else None
    context = tempfile.TemporaryDirectory(prefix="ras-qemu-", dir=work_parent)
    with context as temporary:
        machine = VirtualMachine(
            descriptor, args.arch, accelerator, image, temporary,
            pathlib.Path(args.source_dir).resolve(), args.timeout, args.profile,
        )
        try:
            machine.prepare()
            command = machine.command()
            document.data["qemu_command"] = command
            if not args.quiet:
                print("Guest console: %s" % machine.console_path,
                      file=sys.stderr, flush=True)
            if args.dry_run:
                document.add_test("guest", "skipped", "dry run requested",
                                  {"command": command})
            else:
                started = time.monotonic()
                guest, command, qmp = machine.run()
                document.data["qmp"] = qmp
                guest_tests = guest.get("tests", [])
                if not guest_tests:
                    status = "failed" if guest.get("failed") else "passed"
                    document.add_test(
                        "guest", status, guest.get("reason", ""), guest,
                        time.monotonic() - started,
                    )
                else:
                    for test in guest_tests:
                        document.add_test(
                            test["name"], test["status"],
                            test.get("reason", ""), test.get("evidence", {}),
                            test.get("duration_seconds", 0.0),
                        )
        except (LabError, OSError, subprocess.SubprocessError,
                json.JSONDecodeError) as error:
            document.add_test("guest", "failed", str(error), {
                "console": str(result_dir / "console.log"),
                "qemu_log": str(result_dir / "qemu.log"),
            })
        finally:
            machine.stop()
            result_dir.mkdir(parents=True, exist_ok=True)
            if machine.console_path.exists():
                copy_text_console(machine.console_path,
                                  result_dir / "console.log")
            if machine.qemu_log_path.exists():
                shutil.copy2(machine.qemu_log_path, result_dir / "qemu.log")
    if not args.quiet:
        print("Retained console: %s" % (result_dir / "console.log"),
              file=sys.stderr, flush=True)
    path = document.write(result_dir)
    if not args.quiet:
        print(path)
    return 1 if document.data["totals"]["failed"] else 0


def create_parser(manifest):
    parser = argparse.ArgumentParser(
        description="Run isolated rasdaemon QEMU guest tests.",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST),
                        help="image/architecture manifest")
    parser.add_argument(
        "--cache-dir", default=os.environ.get(
            "RAS_QEMU_CACHE", "~/.cache/rasdaemon/qemu"),
        help="directory containing verified guest images",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("probe", help="inspect host capabilities")
    probe.add_argument("--arch", choices=["all"] + sorted(
        manifest["architectures"]), default="all")

    run = subparsers.add_parser("run", help="run one guest test profile")
    run.add_argument("--arch", choices=sorted(manifest["architectures"]),
                     required=True)
    run.add_argument("--accelerator", choices=("auto", "kvm", "tcg"),
                     default="auto")
    run.add_argument("--profile", choices=("baseline", "injection", "hardened"),
                     default="injection")
    run.add_argument("--result-dir", default="qemu-output")
    run.add_argument("--source-dir", default=os.environ.get(
        "RASDAEMON_SOURCE", str(PROJECT_DIR)),
        help="rasdaemon source checkout to build and test")
    run.add_argument("--work-dir", help="parent for disposable VM data")
    run.add_argument("--timeout", type=int, default=900)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--quiet", action="store_true")
    return parser


def main(argv=None):
    """Program entry point."""
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    known, _unknown = bootstrap.parse_known_args(argv)
    try:
        manifest = load_manifest(known.manifest)
        parser = create_parser(manifest)
        args = parser.parse_args(argv)
        if args.command == "probe":
            return run_probe(args, manifest)
        return run_test(args, manifest)
    except (LabError, OSError, json.JSONDecodeError) as error:
        print("ras-qemu: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Mauro Carvalho Chehab <mchehab+huawei@kernel.org>

"""Run rasdaemon regression tests in isolated QEMU guests."""

import argparse
import dataclasses
import datetime
import hashlib
import html
import json
import os
import pathlib
import platform
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET


FORMAT_VERSION = 1
PROJECT_DIR = pathlib.Path(__file__).resolve().parents[2]
HARNESS_DIR = pathlib.Path(__file__).resolve().parent
RESULT_STYLE = HARNESS_DIR / "results.css"
RESULT_SCRIPT = HARNESS_DIR / "results.js"
RESULT_PORT_NAME = "org.rasdaemon.test.0"
GHES_QMP_PORT = 4445
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


def apply_injection_failures(tests: list[dict], injections: dict[str, dict]) -> None:
    """Apply only explicit host-side injection failures to guest verdicts."""
    aliases = {"mce-hardware-first": "mce", "aer-native": "aer"}

    for test in tests:
        key = aliases.get(test["name"], test["name"])
        injection = injections.get(key, {})

        if "injection_error" in injection:
            test.update(status="failed", kernel="SKIP", rasdaemon="SKIP",
                        reason="Injection failed: " + injection["injection_error"])


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


class GuestWatchdog:
    """Separate QEMU responsiveness, guest heartbeats and scenario progress."""

    def __init__(self, qmp_path: str) -> None:
        self.qmp_path = qmp_path
        self.timing: dict[str, float] = {}
        self.phase = "boot"
        self.samples: list[dict] = []
        self.failure = ""
        self.pending = ""

    def start(self) -> None:
        """Start deadlines when QEMU starts, excluding payload build time."""
        now = time.monotonic()
        self.timing = {"start": now, "console": now, "phase": now, "probe": now}

    def observe(self, text: str) -> None:
        """Consume complete serial lines; heartbeats do not reset phase age."""
        now = time.monotonic()

        if text:
            self.timing["console"] = now

        self.pending += text
        lines = self.pending.split("\n")
        self.pending = lines.pop()[-8192:]

        for line in lines:
            line = ANSI_ESCAPE.sub("", line).strip()

            failures = ("Kernel panic - not syncing", "Oops:", "kernel BUG at",
                        "BUG: KASAN:", "BUG: KCSAN:", "BUG: unable to handle",
                        "BUG: soft lockup", "WARNING: CPU:", "detected stalls on CPUs")

            if any(marker in line for marker in failures):
                self.failure = line

            if "ras-qemu-agent: heartbeat" in line:
                self.timing["heartbeat"] = now
            elif "ras-qemu-agent: " in line:
                phase = line.split("ras-qemu-agent: ", 1)[1]

                if phase != self.phase:
                    self.phase = phase
                    self.timing["phase"] = now

    def snapshot(self, reason: str, detailed: bool = False) -> dict:
        """Bound diagnostics even if the QEMU monitor itself stops responding."""
        sample = {"elapsed": round(time.monotonic() - self.timing["start"], 1),
                  "reason": reason, "phase": self.phase}
        self.samples.append(sample)

        try:
            with QmpClient(self.qmp_path, timeout=2) as qmp:
                sample["transcript"] = qmp.transcript
                sample["status"] = qmp.execute("query-status")

                if detailed:
                    sample["cpus"] = qmp.execute("query-cpus-fast")
                    sample["block"] = qmp.execute("query-block")
                    arguments = {"command-line": "info registers -a"}
                    sample["registers"] = qmp.execute("human-monitor-command", arguments)
        except (LabError, OSError) as error:
            sample["qmp_error"] = str(error)

        return sample

    def check(self) -> None:
        """Print host progress and fail bounded stalls rather than wait silently."""
        now = time.monotonic()

        if self.failure:
            raise LabError(f"Guest kernel failure during {self.phase}: {self.failure}")

        heartbeat = self.timing.get("heartbeat")
        quiet = now - self.timing["console"]
        phase_age = now - self.timing["phase"]

        if heartbeat is not None and now - heartbeat > 90:
            raise LabError(f"Guest heartbeat absent for 90 seconds during {self.phase}")

        if heartbeat is None and quiet > 180:
            raise LabError(f"No guest console activity for 180 seconds during {self.phase}")

        if self.phase != "boot" and phase_age > 360:
            raise LabError(f"Guest phase made no progress for 360 seconds: {self.phase}")

        if now - self.timing["probe"] < 30:
            return

        self.timing["probe"] = now
        sample = self.snapshot("periodic liveness", detailed=quiet > 60)
        status = sample.get("status", {}).get("status", "QMP-unresponsive")
        heartbeat_age = "not received" if heartbeat is None else f"{now - heartbeat:.0f}s"
        print(f"[watchdog] qemu={status}; phase={self.phase}; phase-age={phase_age:.0f}s; "
              f"console-idle={quiet:.0f}s; heartbeat-age={heartbeat_age}",
              file=sys.stderr, flush=True)

        if status in ("guest-panicked", "internal-error", "io-error", "shutdown", "paused", "suspended"):
            raise LabError(f"QEMU entered {status} during {self.phase}")


class HtmlEvidence:
    """Render diagnostic evidence for people while retaining every value."""

    LARGE_TEXT = 800
    BINARY_NAME = re.compile(r"(?:binary|blob|bytes|cper|payload|raw)", re.I)

    def __init__(self) -> None:
        self.templates = []

    @staticmethod
    def _label(value: object) -> str:
        return str(value).replace("_", " ").strip().capitalize()

    def _large_value(self, value: str, binary: bool) -> str:
        identifier = f"evidence-value-{len(self.templates)}"
        kind = "binary data" if binary else "full output"
        css_class = ' class="binary"' if binary else ""
        escaped = html.escape(value)
        self.templates.append(
            f'<template id="{identifier}"><pre{css_class}>{escaped}</pre></template>'
        )

        return (f'<button type="button" class="open-evidence" '
                f'data-evidence="{identifier}">Open {kind}</button>')

    def _scalar(self, value: object, name: str) -> str:
        binary = isinstance(value, bytes) or bool(self.BINARY_NAME.search(name))
        if isinstance(value, bytes):
            text = value.hex(" ")
        elif isinstance(value, bool):
            return "Yes" if value else "No"
        elif value is None:
            return "<em>Not available</em>"
        else:
            text = str(value)

        large = len(text) > self.LARGE_TEXT or text.count("\n") > 12
        if large:
            return self._large_value(text, binary)

        escaped = html.escape(text)
        if "\n" in text:
            return f"<pre>{escaped}</pre>"
        if binary:
            return f'<code class="binary">{escaped}</code>'

        return escaped

    def render(self, value: object, name: str = "") -> str:
        """Render dictionaries and lists as labeled HTML, not JSON syntax."""
        if isinstance(value, dict):
            fields = []

            for key, item in value.items():
                label = html.escape(self._label(key))
                fields.append(f"<dt>{label}</dt><dd>{self.render(item, str(key))}</dd>")

            return '<dl class="evidence-fields">' + "".join(fields) + "</dl>"

        if isinstance(value, (list, tuple)):
            items = [f"<li>{self.render(item, name)}</li>" for item in value]

            return '<ol class="evidence-list">' + "".join(items) + "</ol>"

        return self._scalar(value, name)

    def test_row(self, test: dict) -> str:
        """Render one result row with evidence only when it has content."""
        kernel = test["kernel"]
        daemon = test["rasdaemon"]
        reason = test.get("reason", "").replace("\n", " ")
        values = (test["name"], kernel, daemon, reason)
        cells = [html.escape(str(value)) for value in values]
        evidence = test.get("evidence")
        evidence_html = ""
        kernel_class = "NA" if kernel == "N/A" else kernel
        daemon_class = "NA" if daemon == "N/A" else daemon

        if evidence:
            rendered = self.render(evidence)
            evidence_html = ('<details class="evidence"><summary>Evidence</summary>'
                             + rendered + "</details>")

        return (f'<tr><td>{cells[0]}</td><td class="{kernel_class}">{cells[1]}</td>'
                f'<td class="{daemon_class}">{cells[2]}</td><td class="reason">'
                f'{cells[3]}{evidence_html}</td></tr>')


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

    def add_guest_test(self, test: dict) -> None:
        """Preserve independent kernel and daemon verdicts from the guest."""
        self.add_test(test["name"], test["status"], test.get("reason", ""),
                      test.get("evidence", {}), test.get("duration_seconds", 0.0))
        self.data["tests"][-1].update({key: test[key] for key in ("kernel", "rasdaemon")
                                       if key in test})

    @staticmethod
    def explain_skips(test: dict) -> None:
        """Give every implicit component skip a human-readable explanation."""
        fallback = {
            "passed": "PASS", "failed": "FAIL", "skipped": "SKIP",
        }[test["status"]]
        kernel = test.setdefault("kernel", "SKIP")
        rasdaemon = test.setdefault(
            "rasdaemon",
            fallback if test["name"] != "prerequisites" else "SKIP",
        )
        reason = test.get("reason", "").strip()

        if reason:
            if kernel == "FAIL" and rasdaemon == "SKIP":
                reason += "; rasdaemon was not evaluated after the kernel-side failure"
        elif kernel == "SKIP" and rasdaemon == "SKIP":
            reason = ("Harness or setup check only; it did not exercise kernel "
                      "RAS handling or rasdaemon")
        elif kernel == "SKIP":
            reason = ("Guest setup or rasdaemon-only check; it did not exercise "
                      "kernel RAS handling")
        elif rasdaemon == "SKIP":
            reason = "Kernel-only check; it did not exercise rasdaemon"

        test["reason"] = reason

    def component_totals(self) -> dict:
        """Count independent kernel and rasdaemon verdicts."""
        for test in self.data["tests"]:
            self.explain_skips(test)

        return {
            component: {
                state: sum(
                    test[component] == verdict for test in self.data["tests"]
                )
                for verdict, state in (
                    ("PASS", "passed"),
                    ("FAIL", "failed"),
                    ("SKIP", "skipped"),
                    ("N/A", "not_applicable"),
                )
            }
            for component in ("kernel", "rasdaemon")
        }

    def write_table(self, directory: str) -> None:
        """Publish portable JSON, an Actions summary and a filterable HTML table."""
        badges = {
            "PASS": "🟢 PASS", "FAIL": "🔴 FAIL", "SKIP": "🟡 SKIP",
            "N/A": "⚪ N/A",
        }
        totals = self.component_totals()
        markdown = ["## Component totals", "",
                    "| Component | PASS | FAIL | SKIP | N/A |",
                    "| --- | ---: | ---: | ---: | ---: |",
                    ("| Kernel | {passed} | {failed} | {skipped} | {not_applicable} |"
                     .format(**totals["kernel"])),
                    ("| rasdaemon | {passed} | {failed} | {skipped} | {not_applicable} |"
                     .format(**totals["rasdaemon"])),
                    "", "## Detailed results", "",
                    "| Test | Kernel | rasdaemon | Reason |",
                    "| --- | --- | --- | --- |"]
        rows = []
        renderer = HtmlEvidence()

        for test in self.data["tests"]:
            kernel = test["kernel"]
            daemon = test["rasdaemon"]
            reason = test.get("reason", "").replace("\n", " ")
            fields = (test["name"], badges[kernel], badges[daemon], reason)
            markdown.append("| " + " | ".join(str(value).replace("|", "&#124;")
                                               for value in fields) + " |")
            rows.append(renderer.test_row(test))

        with open(os.path.join(directory, "summary.md"), "w", encoding="utf-8") as stream:
            stream.write("\n".join(markdown) + "\n")

        total_rows = ""

        for component, values in totals.items():
            label = "Kernel" if component == "kernel" else "rasdaemon"
            passed = values["passed"]
            failed = values["failed"]
            skipped = values["skipped"]
            not_applicable = values["not_applicable"]
            total_rows += (f'<tr><th>{label}</th><td class="PASS">{passed}</td>'
                           f'<td class="FAIL">{failed}</td>'
                           f'<td class="SKIP">{skipped}</td>'
                           f'<td class="NA">{not_applicable}</td></tr>')
        page = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAS functional tests</title><link rel="stylesheet" href="results.css">
<script src="results.js" defer></script></head><body>
<h1>Kernel and rasdaemon results</h1>
<h2>Component totals</h2><table><thead><tr><th>Component</th><th>PASS</th>
<th>FAIL</th><th>SKIP</th><th>N/A</th></tr></thead><tbody>''' + total_rows + '''</tbody></table>
<h2>Detailed results</h2>
<p>SKIP means not exercised; inspect the reason. Missing prerequisites are not passes.</p>
<div class="toolbar"><label>Filter tests or status:
<input id="filter" placeholder="e.g. FAIL, cxl, prerequisite"></label>
<button type="button" id="theme" class="theme-toggle" title="Change color theme"
 aria-label="Change color theme">◐ Auto</button></div>
<table id="results"><thead><tr><th>Test</th><th>Kernel</th><th>rasdaemon</th>
<th>Reason / evidence</th></tr></thead><tbody>''' + "".join(rows) + '''</tbody></table>
<dialog id="evidence-dialog"><div class="dialog-toolbar">
<button type="button" id="close-evidence">Close</button></div>
<div id="evidence-dialog-content"></div></dialog>''' + "".join(renderer.templates) + '''
</body></html>'''

        with open(os.path.join(directory, "results.html"), "w", encoding="utf-8") as stream:
            stream.write(page)

        shutil.copyfile(RESULT_STYLE, os.path.join(directory, "results.css"))
        shutil.copyfile(RESULT_SCRIPT, os.path.join(directory, "results.js"))

    def finish(self):
        """Finalize totals and completion timestamp."""
        self.data["component_totals"] = self.component_totals()
        self.data["finished_at"] = utc_now()
        self.data["totals"] = {
            state: sum(test["status"] == state for test in self.data["tests"])
            for state in ("passed", "failed", "skipped")
        }

    def write(self, result_dir):
        """Write result.json, results.log, and junit.xml."""
        self.finish()
        result_dir.mkdir(parents=True, exist_ok=True)
        self.write_table(os.fspath(result_dir))
        json_path = result_dir / "result.json"
        with open(json_path, "w", encoding="utf-8") as stream:
            json.dump(self.data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        log_path = result_dir / "results.log"
        with open(log_path, "w", encoding="utf-8") as stream:
            for result in self.data["tests"]:
                reason = result["reason"]
                suffix = ": " + reason if reason else ""
                stream.write("%s %s%s\n" %
                             (result["status"].upper(), result["name"], suffix))

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
        self.console_offset = 0
        self.overlay_path = self.work_dir / "overlay.qcow2"
        self.payload_dir = self.work_dir / "payload"
        self.block_image = self.work_dir / "block-error.raw"
        self.blkdebug_config = self.work_dir / "blkdebug.conf"
        with open(os.path.join(HARNESS_DIR, "scenarios.json"), encoding="utf-8") as stream:
            self.scenarios: list[dict] = json.load(stream)

        self.injection_evidence: dict[str, dict] = {}
        self.watchdog = GuestWatchdog(os.fspath(self.qmp_path))
        self.fuzz_mode = "random"
        self.fuzz_seed = 1
        self.fuzz_process = None
        self.fuzz_log = None
        self.fuzz_size = 0

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
              "-Denable-arch=all",
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
        shutil.copy2(os.path.join(HARNESS_DIR, "scenarios.json"), self.payload_dir)
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
            "-nic", "none",
            "-no-reboot",
            "-qmp", "unix:%s,server=on,wait=off" % self.qmp_path,
            "-serial", "file:%s" % self.console_path,
            "-drive", "file=%s,if=none,id=ras-os,format=qcow2" % self.overlay_path,
            "-device", "virtio-blk-pci,drive=ras-os,bus=pcie.0",
            "-drive", "file=fat:ro:%s,if=none,id=ras-payload,format=raw,readonly=on" %
            self.payload_dir,
            "-device", "virtio-blk-pci,drive=ras-payload,bus=pcie.0",
            "-device", "virtio-serial-pci,bus=pcie.0",
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
        if self.profile in ("injection", "fuzz") and self.arch == "x86_64":
            command.extend([
                "-machine", "ras=on,cxl=on,cxl-fmw.0.targets.0=ras-cxl-bus,"
                "cxl-fmw.0.size=256M",
                "-object", "memory-backend-ram,id=ras-cxl-mem,size=256M",
                "-device", "pxb-cxl,id=ras-cxl-bus,bus=pcie.0,bus_nr=52",
                "-device", "cxl-rp,id=ras-cxl-port,bus=ras-cxl-bus,port=0,chassis=1,slot=0",
                "-device", "cxl-type3,id=ras-cxl,bus=ras-cxl-port,volatile-memdev=ras-cxl-mem,sn=1",
                "-device", "pcie-root-port,id=ras-aer-root,bus=pcie.0,slot=4",
                "-device",
                "virtio-rng-pci,id=ras-aer,bus=ras-aer-root,aer=on",
            ])
        if self.profile in ("injection", "fuzz"):
            command.extend([
                "-qmp", f"tcp:127.0.0.1:{GHES_QMP_PORT},server=on,wait=off",
            ])
        if self.profile == "injection":
            command.extend([
                "-drive",
                "if=none,format=raw,cache=none,id=ras-block-drive,file="
                "blkdebug:%s:%s" % (self.blkdebug_config, self.block_image),
                "-device",
                "virtio-blk-pci,id=ras-block-error,bus=pcie.0,drive=ras-block-drive,"
                "serial=ras-block-error",
            ])
        return command

    def ready_markers(self):
        """Return scenario readiness markers found in the guest console."""
        try:
            console = self.console_path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return {}
        markers = {}
        for match in re.finditer(
                r"ras-qemu-agent: ([a-z0-9-]+)-ready(?: ([^\r\n]*))?\r?\n",
                console):
            values = {}
            for key, value in re.findall(r"([a-z_]+)=([^ ]+)",
                                         match.group(2) or ""):
                values[key] = value
            markers[match.group(1)] = values
        return markers

    def _print_guest_progress(self):
        """Forward newly written guest-agent progress to the CI console."""
        try:
            with self.console_path.open("r", encoding="utf-8",
                                       errors="replace") as stream:
                stream.seek(self.console_offset)
                text = stream.read()
                self.console_offset = stream.tell()
        except OSError:
            return
        self.watchdog.observe(text)

        for line in text.splitlines():
            line = ANSI_ESCAPE.sub("", line)
            line = NON_TEXT_CONTROL.sub("", line).replace("\r", "").strip()
            if line:
                if line.startswith("ras-qemu-result: "):
                    continue
                print("[guest-console] " + line,
                      file=sys.stderr, flush=True)

    def mce_ready_target(self):
        """Return the guest-selected bank and owned physical address."""
        marker = self.ready_markers().get("mce", {})
        physical = marker.get("physical")
        bank = marker.get("bank")

        if physical is None or bank is None:
            return None

        return {"physical": int(physical, 0), "bank": int(bank, 0)}

    def guest_booted(self):
        """Return whether the guest agent completed payload setup."""
        try:
            console = self.console_path.read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "ras-qemu-agent: payload passed" in console

    def wait_for_guest_boot(self, deadline):
        """Wait for the guest agent before attempting guest injections."""
        while time.monotonic() < deadline:
            self._print_guest_progress()
            self.watchdog.check()
            if self.guest_booted():
                return
            if self.process.poll() is not None:
                raise LabError("QEMU exited before guest boot completed")
            time.sleep(0.2)
        raise LabError("guest boot did not complete before the timeout")

    def inject_mce(self, physical, bank):
        """Inject a corrected hardware-first memory MCE through HMP/QMP."""
        # VAL|EN|MISCV|ADDRV with memory-controller MCACOD 0x90.  With UC
        # clear QEMU updates the bank without raising a synchronous #MC; the
        # guest's shortened polling interval discovers and reports the event.
        status = 0x9c00000000000090
        command_line = "mce 0 %d %#x 0 %#x 0" % (bank, status, physical)
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
            "bank": bank,
            "status": "%#x" % status,
            "response": response,
            "transcript": transcript,
        }

    def inject_aer(self):
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
            "error_type": "BAD_DLLP",
            "response": response,
            "transcript": transcript,
        }

    def inject_scenario(self, scenario: dict) -> None:
        """Inject only after the matching guest consumer reports readiness."""
        name = scenario["name"]
        evidence = self.injection_evidence.setdefault(name, {})
        if name == "cxl-overflow":
            media = next(item for item in self.scenarios if item["name"] == "cxl-media")
            with QmpClient(self.qmp_path, timeout=30) as qmp:
                evidence["transcript"] = qmp.transcript
                # Prevent the guest IRQ handler draining the bounded log
                # until overflow is guaranteed, regardless of host speed.
                qmp.execute("stop")
                try:
                    for _ in range(128):
                        qmp.execute(media["qmp"], media["arguments"])
                finally:
                    qmp.execute("cont")
            return
        if "qmp" in scenario:
            with QmpClient(self.qmp_path, timeout=30) as qmp:
                evidence["transcript"] = qmp.transcript
                qmp.execute(scenario["qmp"], scenario["arguments"])
            return
        if scenario.get("producer") == "hwpoison":
            return
        script = "/opt/qemu/libexec/rasdaemon/ghes_inject.py"
        arguments = scenario.get("helper")
        if arguments is None:
            # UEFI CPER memory section: only error-type is valid, so no
            # arbitrary guest address can be offlined by the kernel.
            if name == "ghes-memory":
                guid = "a5bc1114-6f64-4ede-b863-3e83ed7c83b1"
                payload = bytearray(80)
                struct.pack_into("<Q", payload, 0, 1 << 14)
                payload[72] = 2  # single-bit ECC
            elif name == "ghes-aer":
                guid = "d995e954-bbc1-430f-ad91-b44dcb3c6f35"
                payload = bytearray(208)
                struct.pack_into("<Q", payload, 0, (1 << 3) | (1 << 7))
                bdf = self.ready_markers()[name]["bdf"]
                segment, bus, devfn = bdf.split(":")
                device, function = devfn.split(".")
                payload[31] = int(function, 16)
                payload[32] = int(device, 16)
                struct.pack_into("<H", payload, 33, int(segment, 16))
                payload[35] = int(bus, 16)
                # AER capability registers: correctable status, BAD_DLLP.
                struct.pack_into("<I", payload, 112 + 16, 1 << 7)
            elif name == "ghes-unknown":
                guid = "12345678-1234-5678-9abc-123456789abc"
                payload = bytearray(range(32))
            else:
                raise LabError(f"No producer defined for {name}")
            # Corrected GESB and revision-3 generic data entry.
            gede = (uuid.UUID(guid).bytes_le + struct.pack("<IHBBI", 2, 0x300, 0, 0, len(payload))
                    + bytes(44))
            gesb = struct.pack("<IIIII", 0x12, 0, 0, len(gede) + len(payload), 2)
            def dump(label: str, data: bytes) -> str:
                lines = [label + ":"]

                for offset in range(0, len(data), 16):
                    hex_data = data[offset:offset + 16].hex(" ")
                    lines.append(f"    {offset:08x}  {hex_data:47s}  ................")

                return "\n".join(lines)
            raw = ("GUID: " + guid + "\n" + dump("Generic Error Status Block", gesb)
                   + "\n" + dump("Generic Error Data Entry", gede)
                   + "\n" + dump("Payload", payload) + "\n")
            raw_path = os.path.join(self.work_dir, name + ".cper")

            with open(raw_path, "w", encoding="utf-8") as stream:
                stream.write(raw)

            evidence["raw_cper"] = raw
            arguments = ["raw", "--file", raw_path]
        command = [sys.executable, script, "--debug", "--host", "127.0.0.1",
                   "--port", str(GHES_QMP_PORT)] + arguments
        evidence["command"] = command
        completed = subprocess.run(command, check=False, text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=45)
        evidence["output"] = completed.stdout
        if completed.returncode:
            raise LabError("GHES injection failed: %s" % completed.stdout.strip())

    def poll_fuzz(self) -> None:
        """Run the weekly generator without blocking watchdog or result collection."""
        if "fuzz" not in self.ready_markers():
            return

        corpus = os.path.join(self.work_dir, "fuzz-corpus.jsonl")

        if self.fuzz_process is None:
            command = [sys.executable, "-u", os.path.join(HARNESS_DIR, "fuzz.py"),
                       "--mode", self.fuzz_mode, "--seed", str(self.fuzz_seed),
                       "--count", "1000", "--port", str(GHES_QMP_PORT), "--corpus", corpus]
            self.injection_evidence["fuzz"] = {"command": command, "seed": self.fuzz_seed,
                                                "mode": self.fuzz_mode}
            self.fuzz_log = open(os.path.join(self.work_dir, "fuzz.log"), "w", encoding="utf-8")
            self.fuzz_process = subprocess.Popen(command, stdout=self.fuzz_log,
                                                 stderr=subprocess.STDOUT, start_new_session=True)

        size = os.path.getsize(corpus) if os.path.isfile(corpus) else 0

        if size != self.fuzz_size:
            self.fuzz_size = size
            self.watchdog.phase = f"fuzz-{self.fuzz_mode} corpus-bytes={size}"
            self.watchdog.timing["phase"] = time.monotonic()

        result = self.fuzz_process.poll()

        if result is None or "complete" in self.injection_evidence["fuzz"]:
            return

        self.injection_evidence["fuzz"]["returncode"] = result

        if result:
            raise LabError(f"Fuzz generator failed with status {result}; see fuzz.log and corpus")

        # This unique CPER is absent from the random generator's family list.
        # Its matching row proves kernel delivery and rasdaemon recording
        # still work after the entire fuzz batch, not merely that QMP replied.
        self.inject_scenario({"name": "ghes-unknown"})
        self.injection_evidence["fuzz"]["complete"] = True

    def run(self):
        """Run until the guest emits one JSON document or the timeout expires."""
        command = self.command()
        self.qemu_log = self.qemu_log_path.open("wb")
        self.process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=self.qemu_log,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.watchdog.start()
        print("[guest] QEMU process started; waiting for guest boot",
              file=sys.stderr, flush=True)
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

            self.wait_for_guest_boot(deadline)

            while time.monotonic() < deadline:
                self._print_guest_progress()
                self.watchdog.check()
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
            print("[guest] result channel connected; waiting for test results",
                  file=sys.stderr, flush=True)
            while time.monotonic() < deadline:
                self._print_guest_progress()
                self.watchdog.check()
                if self.profile == "fuzz":
                    self.poll_fuzz()
                if self.profile == "injection":
                    markers = self.ready_markers()
                    for scenario in self.scenarios:
                        if (scenario["name"] in markers and
                                scenario["name"] not in self.injection_evidence):
                            try:
                                self.inject_scenario(scenario)
                            except (LabError, OSError, subprocess.SubprocessError) as error:
                                self.injection_evidence[scenario["name"]]["injection_error"] = str(error)
                                print(f"Injection {scenario['name']} failed: {error}",
                                      file=sys.stderr, flush=True)
                if self.profile == "injection" and self.arch == "x86_64" and \
                   not mce_injected:
                    target = self.mce_ready_target()
                    if target is not None:
                        try:
                            qmp_evidence["mce"] = self.inject_mce(
                                target["physical"], target["bank"])
                        except (LabError, OSError) as error:
                            qmp_evidence["mce"] = {"injection_error": str(error)}
                        self.injection_evidence["mce"] = qmp_evidence["mce"]
                        mce_injected = True
                if self.profile == "injection" and self.arch == "x86_64" and \
                   not aer_injected and "aer" in self.ready_markers():
                    try:
                        qmp_evidence["aer"] = self.inject_aer()
                    except (LabError, OSError) as error:
                        qmp_evidence["aer"] = {"injection_error": str(error)}
                    self.injection_evidence["aer"] = qmp_evidence["aer"]
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
            qmp_evidence["scenarios"] = self.injection_evidence
            return json.loads(payload.decode("utf-8")), command, qmp_evidence
        except (LabError, OSError, ValueError, subprocess.SubprocessError):
            self.watchdog.snapshot("failure before stopping QEMU", detailed=True)
            raise
        finally:
            if client:
                client.close()
            self.stop()

    def stop(self):
        """Stop only the process group created by this instance."""
        if self.fuzz_process is not None and self.fuzz_process.poll() is None:
            os.killpg(self.fuzz_process.pid, signal.SIGKILL)
            self.fuzz_process.wait(timeout=10)

        if self.fuzz_log is not None:
            self.fuzz_log.close()
            self.fuzz_log = None

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
    config_status = os.path.join(HARNESS_DIR, "..", "kernel", "config-status.tsv")

    if os.path.isfile(config_status):
        with open(config_status, encoding="utf-8") as stream:
            for line in list(stream)[1:]:
                symbol, requested, actual = line.strip().split("\t")
                verdict = "PASS" if actual == requested else "SKIP"
                if actual == requested:
                    reason = f"Kernel option is {actual} as requested"
                else:
                    reason = (f"Kernel option requested {requested}, but the built "
                              f"kernel has {actual}")
                document.add_test(f"Enable Kernel {symbol} config",
                                  "passed" if verdict == "PASS" else "skipped", reason)
                document.data["tests"][-1].update(kernel=verdict, rasdaemon="N/A")

    result_dir = pathlib.Path(args.result_dir).resolve()
    try:
        accelerator = choose_accelerator(args.accelerator, checks)
        document.data["accelerator"] = accelerator
    except LabError as error:
        document.add_test("prerequisites", "skipped", str(error))
        document.write(result_dir)
        return 1

    required = [check for check in checks
                if check.name in ("qemu", "qemu-img", "image") or
                check.name.startswith("firmware-")]
    missing = [check.reason for check in required if not check.available]
    if missing:
        document.add_test("prerequisites", "skipped", "; ".join(missing))
        document.write(result_dir)
        return 1
    document.add_test("prerequisites", "passed")

    image = next(check.value for check in checks if check.name == "image")
    work_parent = pathlib.Path(args.work_dir).expanduser() if args.work_dir else None
    context = tempfile.TemporaryDirectory(prefix="ras-qemu-", dir=work_parent)
    with context as temporary:
        machine = VirtualMachine(
            descriptor, args.arch, accelerator, image, temporary,
            pathlib.Path(args.source_dir).resolve(), args.timeout, args.profile,
        )
        machine.fuzz_mode = getattr(args, "fuzz_mode", "random")
        machine.fuzz_seed = getattr(args, "fuzz_seed", 1)
        try:
            if not args.quiet:
                print("Preparing guest and building rasdaemon payload",
                      file=sys.stderr, flush=True)
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
                if not args.quiet:
                    print("Starting QEMU with accelerator %s" % accelerator,
                          file=sys.stderr, flush=True)
                started = time.monotonic()
                guest, command, qmp = machine.run()
                document.data["qmp"] = qmp
                document.data["guest"] = guest.get("guest", {})
                guest_tests = guest.get("tests", [])
                if not guest_tests:
                    document.add_test(
                        "guest", "failed", "guest returned no test cases", guest,
                        time.monotonic() - started,
                    )
                else:
                    for test in guest_tests:
                        document.add_guest_test(test)
                if args.profile == "injection":
                    required = {scenario["name"] for scenario in machine.scenarios}
                    required.update(("mce-hardware-first", "aer-native", "block-io-native"))
                    reported = {test["name"] for test in guest_tests}
                    missing = sorted(required - reported)
                    if missing:
                        document.add_test("coverage-contract", "failed",
                                          "Scenarios were not reported: " + ", ".join(missing))
        except (LabError, OSError, subprocess.SubprocessError,
                json.JSONDecodeError) as error:
            if os.path.isfile(machine.console_path):
                with open(machine.console_path, encoding="utf-8", errors="replace") as stream:
                    for line in stream:
                        if "ras-qemu-result: " in line:
                            try:
                                test = json.loads(line.split("ras-qemu-result: ", 1)[1])
                                document.add_guest_test(test)
                            except (ValueError, KeyError, TypeError):
                                pass

            document.add_test("guest", "failed", str(error), {
                "console": str(result_dir / "console.log"),
                "qemu_log": str(result_dir / "qemu.log"),
            })
            document.data["tests"][-1].update(kernel="FAIL" if machine.watchdog.failure else "SKIP",
                                               rasdaemon="SKIP")
            expected = [scenario["name"] for scenario in machine.scenarios]
            expected += ["mce-hardware-first", "aer-native", "block-io-native"]
            expected = ["fuzz"] if args.profile == "fuzz" else expected
            reported = {test["name"] for test in document.data["tests"]}

            if args.profile in ("injection", "fuzz"):
                for name in expected:
                    if name not in reported:
                        active = machine.watchdog.phase.startswith(name)
                        status = "failed" if active else "skipped"
                        reason = str(error) if active else "Not run after guest/infrastructure failure"
                        document.add_test(name, status, reason)
                        kernel = "FAIL" if active and machine.watchdog.failure else "SKIP"
                        document.data["tests"][-1].update(kernel=kernel, rasdaemon="SKIP")
        finally:
            machine.stop()
            document.data["injections"] = machine.injection_evidence
            document.data["watchdog"] = machine.watchdog.samples
            apply_injection_failures(document.data["tests"], machine.injection_evidence)

            result_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("fuzz.log", "fuzz-corpus.jsonl"):
                source = os.path.join(machine.work_dir, filename)

                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(result_dir, filename))
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
    parser.add_argument("--manifest", required=True,
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
    run.add_argument("--profile", choices=("baseline", "injection", "fuzz", "hardened"),
                     default="injection")
    run.add_argument("--fuzz-mode", choices=("random", "zero", "boundaries"), default="random")
    run.add_argument("--fuzz-seed", type=int, default=1)
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
    bootstrap.add_argument("--manifest", required=True)
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

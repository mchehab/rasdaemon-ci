# SPDX-License-Identifier: GPL-2.0-only
"""Exercise real ERST storage and both rasdaemon pstore lookup layouts.

Only invoked by the disposable guest agent, never on the host under test.
Persistence here means surviving filesystem unmount/remount, not a power cycle.
"""

import os
import signal
import struct
import subprocess
import tempfile
import time
import uuid


def mce_record() -> bytes:
    """Encode the x86 Linux struct mce ABI (120 bytes including tail padding)."""
    record = bytearray(120)
    struct.pack_into("<Q", record, 0, 0x9c00000000000090)
    struct.pack_into("<Q", record, 16, 0x12345000)
    struct.pack_into("<Q", record, 48, 1700000000)
    struct.pack_into("<I", record, 60, 0x50654)
    record[65] = 1
    record[67] = 1
    struct.pack_into("<Q", record, 80, 4)
    return bytes(record)


def pstore_record(record_id: int) -> bytes:
    """Wrap an MCE in Linux's CPER pstore header and section descriptor."""
    payload = mce_record()
    header = bytearray(128)
    struct.pack_into("<4sHIHIII", header, 0, b"CPER", 0x100,
                     0xffffffff, 1, 2, 0, 200 + len(payload))
    header[64:80] = uuid.UUID("75a574e3-5052-4b29-8a8e-be2c6490b89d").bytes_le
    header[80:96] = uuid.UUID("e8f56ffe-919c-4cc5-ba88-65abe14913bb").bytes_le
    struct.pack_into("<QI", header, 96, record_id, 2)
    section = bytearray(72)
    struct.pack_into("<IIHBBI", section, 0, 200, len(payload), 0x100, 0, 0, 1)
    section[16:32] = uuid.UUID("fe08ffbe-95e4-4be7-bc73-4096044a38fc").bytes_le
    struct.pack_into("<I", section, 48, 2)
    return bytes(header + section) + payload


class ErstCheck:
    """Retain storage evidence separately from the rasdaemon consumer verdict."""

    def __init__(self, results, binary: str, environment: dict, wait_ready):
        self.results = results
        self.binary = binary
        self.environment = dict(environment)
        self.wait_ready = wait_ready
        self.evidence = {"commands": [], "layouts": [], "persistence": "filesystem remount"}
        self.mounts: list[str] = []
        self.restore_pstore = False

    def command(self, command: list[str]) -> None:
        """Keep every command and its output, including failed setup commands."""
        completed = subprocess.run(command, check=False, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.evidence["commands"].append({"command": command, "output": completed.stdout,
                                          "returncode": completed.returncode})
        if completed.returncode:
            raise RuntimeError("ERST command failed: " + " ".join(command))

    def mount(self, kind: str, target: str) -> None:
        """Track only mounts created by this check for cleanup."""
        self.command(["mount", "-t", kind, kind, target])
        self.mounts.append(target)

    def unmount(self) -> None:
        """Remove the most recently created mount."""
        self.command(["umount", self.mounts[-1]])
        self.mounts.pop()

    def consume(self, directory: str, label: str, delete: bool) -> None:
        """Demand a decoded ERST record, correct address and clean shutdown."""
        logfile = os.path.join(directory, label + ".log")
        config = os.path.join(directory, label + ".env")
        environment = dict(self.environment, ERST_DELETE=str(int(delete)))
        with open(config, "w", encoding="utf-8") as stream:
            stream.write("".join(f"{key}={value}\n" for key, value in environment.items()
                                 if key != "PYTHONDONTWRITEBYTECODE"))

        command = [self.binary, "--foreground", "--record", "--config", config]
        with open(logfile, "w", encoding="utf-8") as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, **environment), start_new_session=True)
            try:
                event = "/sys/kernel/tracing/instances/rasdaemon/events/mce/mce_record/enable"
                self.wait_ready(process, logfile, [event])
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                with open(logfile, encoding="utf-8", errors="replace") as output:
                    text = output.read()
                self.evidence["layouts"].append({"layout": label, "delete": delete,
                                                 "command": command, "output": text,
                                                 "returncode": process.returncode})

        matching = [line for line in text.splitlines() if "mce_erst_record:" in line
                    and "addr= 12345000" in line]
        if process.returncode or len(matching) != 1:
            raise RuntimeError(f"ERST {label}: expected exactly one decoded fixture record")
        if delete and ("Deleted file " not in text or "Error deleting file " in text):
            raise RuntimeError("ERST deletion was not reported as successful")

    def exercise(self, directory: str) -> None:
        """Store through ERST, then read through flat and nested pstore mounts."""
        root = "/sys/fs/pstore"
        self.command(["modprobe", "erst_dbg"])
        # A second mount reuses the boot-time pstore superblock and its empty
        # file list. Drop that mount in this disposable guest so mounting after
        # the write actually re-reads ERST. Restore the original mount on exit.
        if os.path.ismount(root):
            self.command(["umount", root])
            self.restore_pstore = True

        record_id = 0x5241534349
        record = pstore_record(record_id)
        self.evidence.update(record_id=record_id, cper_hex=record.hex(),
                             mce_hex=mce_record().hex())
        # The QEMU runner gives this guest a new, dedicated backing file.
        # Refuse to proceed if an unexpected persistent record is present.
        with open("/dev/erst_dbg", "rb", buffering=0) as stream:
            if stream.read(16384):
                raise RuntimeError("Refusing to alter a nonempty ERST store")
        with open("/dev/erst_dbg", "wb", buffering=0) as stream:
            if stream.write(record) != len(record):
                raise RuntimeError("Short ERST record write")
        with open("/dev/erst_dbg", "rb", buffering=0) as stream:
            returned = stream.read(16384)
        self.evidence["erst_readback_hex"] = returned.hex()
        # Linux erst_write() stamps the serialization signature into the
        # persistence-information field before writing the record to storage.
        expected = bytearray(record)
        expected[108:110] = b"ER"
        if returned != expected:
            raise RuntimeError("ERST readback differs from the written CPER")

        os.makedirs(root, exist_ok=True)
        self.mount("pstore", root)
        filename = f"mce-erst-{record_id}"
        path = os.path.join(root, filename)
        with open(path, "rb") as stream:
            if stream.read() != mce_record():
                raise RuntimeError("pstore did not expose the expected MCE payload")
        self.evidence["kernel_observed"] = True
        self.consume(directory, "flat", False)
        self.unmount()

        # Hide any pre-existing root mount without unmounting somebody else's
        # mount, then expose the real pstore beneath the legacy erst path.
        self.mount("tmpfs", root)
        nested = os.path.join(root, "erst")
        os.mkdir(nested)
        self.mount("pstore", nested)
        self.consume(directory, "nested", False)
        self.consume(directory, "nested-delete", True)
        if os.path.exists(os.path.join(nested, filename)):
            raise RuntimeError("ERST_DELETE left the pstore record behind")
        self.unmount()
        self.mount("pstore", nested)
        if os.path.exists(os.path.join(nested, filename)):
            raise RuntimeError("Deleted record reappeared after pstore remount")
        with open("/dev/erst_dbg", "rb", buffering=0) as stream:
            if stream.read(16384):
                raise RuntimeError("Deletion did not clear the ERST backing store")

    def execute(self) -> None:
        """Report failures without preventing unrelated guest tests."""
        self.results.start("erst-persistence")
        started = time.monotonic()
        status, reason = "passed", ""
        with tempfile.TemporaryDirectory(prefix="ras-erst-") as directory:
            try:
                self.exercise(directory)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                status, reason = "failed", str(error)
            finally:
                while self.mounts:
                    target = self.mounts.pop()
                    try:
                        self.command(["umount", target])
                    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                        status, reason = "failed", f"ERST cleanup: {error}"
                if self.restore_pstore:
                    try:
                        self.command(["mount", "-t", "pstore", "pstore", "/sys/fs/pstore"])
                    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                        status, reason = "failed", f"ERST restore mount: {error}"

        self.results.add("erst-persistence", status, reason, self.evidence,
                         time.monotonic() - started,
                         kernel="PASS" if self.evidence.get("kernel_observed") else "FAIL",
                         rasdaemon="PASS" if status == "passed" else "FAIL")

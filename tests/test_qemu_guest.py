# SPDX-License-Identifier: GPL-2.0-only
"""Checks for false positives in guest hardware-event evidence."""

import importlib.util
import os
import tempfile
import unittest
from unittest.mock import Mock, mock_open, patch
import subprocess


MODULE_PATH = os.path.join(os.path.dirname(__file__), "qemu", "guest", "agent.py")
SPEC = importlib.util.spec_from_file_location("guest_agent", MODULE_PATH)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class RecordedEventTest(unittest.TestCase):
    """Reject unrelated rows that happen to use the expected table."""

    def test_sel_checks_preserve_oem_manufacturer_wire_order(self) -> None:
        """OEM ID 0x00cd3a is little endian in the BMC response."""
        scenario = Mock()
        scenario.paths = {"database": "/unused/test.db"}
        scenario.scenario = {"name": "consumer-arm-sel"}
        scenario.evidence = {"bdf": "0000:01:00.0"}
        checks = agent.consumers.ConsumerChecks(scenario)
        replies = ["01 00 00 00 c0 00 00 00 00 3a cd 00 c0 bf 00 00 01 00",
                   "ff ff 01 00 fb 20 00 00 00 00 01 00 00 01 01 00 ff 02"]

        with patch.object(agent.consumers.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, reply) for reply in replies]):
            checks.check_sel("passed")

        self.assertEqual([call.args[:2] for call in scenario.results.add.call_args_list],
                         [("consumer-ampere-oem-sel", "passed"),
                          ("consumer-openbmc-unified-sel", "passed")])

    def test_erst_readback_allows_only_the_kernel_signature(self) -> None:
        """Kernel persistence metadata is expected; payload corruption is not."""
        record = agent.erst.pstore_record(0x5241534349)
        returned = bytearray(record)
        returned[108:110] = b"ER"

        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                reply = bytearray(returned)
                if corrupt:
                    reply[-1] ^= 1
                opener = mock_open()
                opener.return_value.read.side_effect = [b"", bytes(reply)]
                opener.return_value.write.return_value = len(record)
                check = agent.erst.ErstCheck(Mock(), "/unused", {}, Mock())
                expected = "readback differs" if corrupt else "readback accepted"

                with patch("builtins.open", opener), patch.object(check, "command"), \
                        patch.object(agent.erst.os.path, "ismount", return_value=True), \
                        patch.object(agent.erst.os, "makedirs"), \
                        patch.object(check, "mount", side_effect=RuntimeError("readback accepted")):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        check.exercise("/unused")
                self.assertTrue(check.restore_pstore)

    def test_erst_restores_boot_mount_after_failure(self) -> None:
        """A failed fixture must not leave the guest's pstore unmounted."""
        check = agent.erst.ErstCheck(Mock(), "/unused", {}, Mock())
        check.restore_pstore = True
        check.mounts = ["/sys/fs/pstore", "/sys/fs/pstore/erst"]
        with patch.object(check, "exercise", side_effect=RuntimeError("fixture failed")), \
                patch.object(check, "command") as command:
            check.execute()
        self.assertEqual([call.args[0] for call in command.call_args_list], [
            ["umount", "/sys/fs/pstore/erst"], ["umount", "/sys/fs/pstore"],
            ["mount", "-t", "pstore", "pstore", "/sys/fs/pstore"]])
        self.assertEqual(check.results.add.call_args.args[1:3], ("failed", "fixture failed"))

    def test_readiness_requires_current_consumer(self) -> None:
        """Stale enabled flags and a half-built database are not readiness."""
        process = Mock()
        process.poll.return_value = None
        startup = "Database backend started: sqlite3.\n"

        with tempfile.TemporaryDirectory() as directory:
            event = os.path.join(directory, "enable")
            log = os.path.join(directory, "daemon.log")

            with open(event, "w", encoding="utf-8") as stream:
                stream.write("1\n")

            with open(log, "w", encoding="utf-8") as stream:
                stream.write(startup)

            with self.assertRaisesRegex(RuntimeError, "startup timed out"):
                agent.wait_daemon_ready(process, log, [event], timeout=0.01)

            with open(log, "a", encoding="utf-8") as stream:
                stream.write("Listening to events for cpus 0 to 1\n")

            with patch.object(agent.sqlite3, "connect") as connect:
                agent.wait_daemon_ready(process, log, [event])
                connect.assert_not_called()

            process.poll.return_value = 1

            with self.assertRaisesRegex(RuntimeError, "exited during startup"):
                agent.wait_daemon_ready(process, log, [event])

    def test_empty_poison_list_is_not_injection_coverage(self) -> None:
        """A successful empty mailbox query is not a poison error."""
        scenario = {"name": "cxl-poison"}
        self.assertFalse(agent.matching_record(scenario, {"dpa": 0, "dpa_length": 0}, {}))
        self.assertFalse(agent.matching_record(scenario, {"dpa": 8192, "dpa_length": 64}, {}))
        self.assertTrue(agent.matching_record(scenario, {"dpa": 4096, "dpa_length": 64}, {}))

    def test_sigbus_must_be_hardware_generated_for_the_target_process(self) -> None:
        """kill(SIGBUS) and another process's signal must not pass."""
        scenario = {"name": "memory-sigbus"}
        evidence = {"holder_pid": 123}
        row = {"sig": 7, "code": 4, "pid": 123}
        self.assertTrue(agent.matching_record(scenario, row, evidence))
        self.assertFalse(agent.matching_record(scenario, dict(row, code=0), evidence))
        self.assertFalse(agent.matching_record(scenario, dict(row, pid=456), evidence))

    def test_memory_failure_must_reference_the_poisoned_pfn(self) -> None:
        """The row must identify the guest's disposable page."""
        scenario = {"name": "memory-failure"}
        self.assertTrue(agent.matching_record(scenario, {"pfn": "0x123"}, {"pfn": 0x123}))
        self.assertFalse(agent.matching_record(scenario, {"pfn": "0x456"}, {"pfn": 0x123}))


if __name__ == "__main__":
    unittest.main()

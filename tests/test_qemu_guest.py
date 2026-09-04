# SPDX-License-Identifier: GPL-2.0-only
"""Checks for false positives in guest hardware-event evidence."""

import importlib.util
import os
import tempfile
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = os.path.join(os.path.dirname(__file__), "qemu", "guest", "agent.py")
SPEC = importlib.util.spec_from_file_location("guest_agent", MODULE_PATH)
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class RecordedEventTest(unittest.TestCase):
    """Reject unrelated rows that happen to use the expected table."""

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

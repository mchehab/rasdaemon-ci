# SPDX-License-Identifier: GPL-2.0-only
"""Verify watchdog decisions, replay retention and per-component reporting."""

import contextlib
import base64
import io
import json
import os
import socket
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from tests.qemu import fuzz, ras_qemu
from tests.qemu.guest import agent


class WatchdogTest(unittest.TestCase):
    """Console silence and heartbeat silence carry different evidence."""

    def setUp(self) -> None:
        self.clock = patch.object(ras_qemu.time, "monotonic", return_value=0)
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.monitor = ras_qemu.GuestWatchdog("unused")
        self.monitor.start()

    def test_heartbeat_does_not_hide_stuck_phase(self) -> None:
        """An agent thread can be alive while the test command never completes."""
        self.now.return_value = 1
        self.monitor.observe("ras-qemu-agent: cxl-media-ready\n")
        self.now.return_value = 370
        self.monitor.observe("ras-qemu-agent: heartbeat phase=cxl-media-ready\n")

        with self.assertRaisesRegex(ras_qemu.LabError, "phase made no progress"):
            self.monitor.check()

    def test_kernel_console_does_not_hide_lost_userspace_heartbeat(self) -> None:
        """A kernel logging repeatedly is not proof of guest userspace health."""
        self.monitor.observe("ras-qemu-agent: heartbeat phase=starting\n")
        self.now.return_value = 91
        self.monitor.observe("[123] kernel still printing\n")

        with self.assertRaisesRegex(ras_qemu.LabError, "heartbeat absent"):
            self.monitor.check()

    def test_stack_depth_message_is_not_a_failure(self) -> None:
        """Task stack diagnostics do not prove suspend, panic or a lockup."""
        self.monitor.observe("[2376] systemd-hiberna used greatest stack depth: 12584 bytes left\n")
        self.monitor.check()
        self.assertFalse(self.monitor.failure)

    def test_kernel_assertion_is_failure(self) -> None:
        """Kernel testing must fail even when a warning did not panic the VM."""
        self.monitor.observe("[99] WARNING: CPU: 1 PID: 22 at ghes.c\n")

        with self.assertRaisesRegex(ras_qemu.LabError, "Guest kernel failure"):
            self.monitor.check()

    def test_unresponsive_qmp_diagnostics_are_retained(self) -> None:
        """Failure evidence must survive a wedged monitor too."""
        connection = MagicMock()
        connection.__enter__.side_effect = socket.timeout("monitor timeout")

        with patch.object(ras_qemu, "QmpClient", return_value=connection):
            sample = self.monitor.snapshot("test failure", detailed=True)

        self.assertIn("monitor timeout", sample["qmp_error"])
        self.assertIs(self.monitor.samples[-1], sample)


class ResultTableTest(unittest.TestCase):
    """Results are independently classified and safe to display."""

    def test_payload_result_emits_host_boot_marker(self) -> None:
        """The host must see the readiness text, not just an in-memory result."""
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            agent.Results().add("payload", "passed")

        self.assertIn("ras-qemu-agent: payload passed\n", output.getvalue())
        self.assertIn("ras-qemu-result:", output.getvalue())

    def test_kernel_precondition_skips_without_aborting_results(self) -> None:
        """A missing event must not prevent recording the next scenario."""
        results = agent.Results()

        with contextlib.redirect_stdout(io.StringIO()):
            results.add("one", "failed", "guest kernel lacks ras:mc_event")
            results.add("two", "passed", evidence={"sqlite_count": 1})

        self.assertEqual(results.tests[0]["status"], "skipped")
        self.assertEqual(results.tests[0]["kernel"], "SKIP")
        self.assertEqual(results.tests[1]["kernel"], "PASS")

    def test_html_escapes_guest_text_and_preserves_component_verdicts(self) -> None:
        """A decoder message is data, including when it contains HTML."""
        document = ras_qemu.ResultDocument("x86_64", "tcg", "injection")
        document.add_guest_test({"name": "case", "status": "failed", "kernel": "PASS",
                                 "rasdaemon": "FAIL", "reason": "<script>bad</script>"})

        with tempfile.TemporaryDirectory() as directory:
            document.write_table(directory)

            with open(os.path.join(directory, "results.html"), encoding="utf-8") as stream:
                page = stream.read()

            with open(os.path.join(directory, "summary.md"), encoding="utf-8") as stream:
                summary = stream.read()

        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", page)
        self.assertIn("<h2>Component totals</h2>", page)
        self.assertIn('<tr><th>Kernel</th><td class="PASS">1</td>', page)
        self.assertIn('<tr><th>rasdaemon</th><td class="PASS">0</td>', page)
        self.assertIn("| Kernel | 1 | 0 | 0 |", summary)
        self.assertIn("| rasdaemon | 0 | 1 | 0 |", summary)
        self.assertIn("🟢 PASS | 🔴 FAIL", summary)
        self.assertNotIn("<summary>Evidence</summary>", page)

    def test_evidence_is_human_readable_and_large_values_use_dialog(self) -> None:
        """Evidence is labeled for people while long binary text stays complete."""
        payload = "a5" * 500
        evidence = {
            "sqlite_count": 2,
            "kernel_observed": True,
            "command_output": "first line\nsecond line",
            "raw_payload": payload,
            "rows": [{"record_id": 7, "status": "stored"}],
        }
        document = ras_qemu.ResultDocument("x86_64", "tcg", "injection")
        document.add_test("case", "passed", evidence=evidence)

        with tempfile.TemporaryDirectory() as directory:
            document.write_table(directory)

            with open(os.path.join(directory, "results.html"), encoding="utf-8") as stream:
                page = stream.read()

            with open(os.path.join(directory, "results.css"), encoding="utf-8") as stream:
                stylesheet = stream.read()

            with open(os.path.join(directory, "results.js"), encoding="utf-8") as stream:
                script = stream.read()

        self.assertIn("<summary>Evidence</summary>", page)
        self.assertIn("<dt>Sqlite count</dt><dd>2</dd>", page)
        self.assertIn("<dt>Kernel observed</dt><dd>Yes</dd>", page)
        self.assertIn("<pre>first line\nsecond line</pre>", page)
        self.assertIn("Open binary data", page)
        self.assertIn(payload, page)
        self.assertNotIn('&quot;sqlite_count&quot;', page)
        self.assertIn('prefers-color-scheme: dark', stylesheet)
        self.assertIn('html[data-theme="dark"]', stylesheet)
        self.assertIn('localStorage.setItem("rasdaemon-theme"', script)
        self.assertIn('☾ Nocturn', script)
        self.assertIn('class="theme-toggle"', page)
        self.assertIn("showModal()", script)


class FuzzParametersTest(unittest.TestCase):
    """Pin upstream helper size semantics and pre-injection corpus retention."""

    def test_zero_and_random_lanes_use_variable_sizes(self) -> None:
        """Zero filling must vary length too; the helper's upper limit is exclusive."""
        random_args = fuzz.case_parameters(0, ["arm"], "random")
        zero_args = fuzz.case_parameters(0, ["arm"], "zero")
        self.assertNotIn("--zero", random_args)
        self.assertIn("--zero", zero_args)
        self.assertEqual(random_args[random_args.index("--max-size") + 1], "513")

    def test_boundary_lane_visits_each_family_and_size(self) -> None:
        """Avoid leaving structure boundaries to a random chance."""
        families = ["arm", "pcie", "memory"]
        observed = set()

        for index in range(len(families) * len(fuzz.BOUNDARIES)):
            arguments = fuzz.case_parameters(index, families, "boundaries")
            family = arguments[arguments.index("-T") + 1]
            size = int(arguments[arguments.index("--min-size") + 1])
            observed.add((family, size))

        self.assertEqual(observed, {(family, size) for family in families for size in fuzz.BOUNDARIES})

    def test_cper_is_persisted_before_a_failed_send(self) -> None:
        """A send failure must leave the triggering bytes, not just the seed."""
        class FakeMonitor:
            """The only mocked part is the helper transport."""

            def __init__(self, *_args: object) -> None:
                pass

            def get_gede(self, *_args: object) -> bytes:
                """Provide a known header."""
                return bytes(72)

            def get_gebs(self, *_args: object) -> bytes:
                """Provide a known status block."""
                return bytes(20)

            def send_cper(self, *_args: object, **_kwargs: object) -> None:
                """Simulate QMP failing after generation."""
                raise RuntimeError("delivery failed")

            def _close(self) -> None:
                """Mirror the helper's cleanup interface."""

        class FakeGenerator:
            """Accept the real adapter's CLI and invoke its transport."""

            def __init__(self, subparsers: object) -> None:
                self.types = {"arm": {}}
                parser = subparsers.add_parser("fuzzy")

                for flag in ("-c", "-t", "-T", "--min-size", "--max-size"):
                    parser.add_argument(flag)

                parser.add_argument("--zero", action="store_true")

            def send_cper(self, _args: object) -> None:
                """Exercise the adapter's actual persistence method."""
                fake_fuzzy.qmp().send_cper("guid", b"payload")

        fake_fuzzy = types.SimpleNamespace(FuzzyError=FakeGenerator)
        modules = {"fuzzy_error": fake_fuzzy, "qmp_helper": types.SimpleNamespace(qmp=FakeMonitor)}

        with tempfile.TemporaryDirectory() as directory:
            corpus = os.path.join(directory, "corpus.jsonl")
            arguments = ["fuzz.py", "--mode", "zero", "--seed", "5", "--count", "1",
                         "--port", "1234", "--corpus", corpus]

            with patch.dict("sys.modules", modules), patch("sys.argv", arguments):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                        fuzz.main()

            with open(corpus, encoding="utf-8") as stream:
                entry = json.loads(stream.readline())

        self.assertEqual(entry["seed"], 5)
        self.assertEqual(entry["index"], 0)
        self.assertEqual(base64.b64decode(entry["cper"]), bytes(92) + b"payload")


if __name__ == "__main__":
    unittest.main()

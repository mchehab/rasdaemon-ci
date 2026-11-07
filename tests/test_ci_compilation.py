# SPDX-License-Identifier: GPL-2.0-only
"""Cache settings and payload reuse must match the tested source and architecture."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "qemu"))
from ci import compilation  # pylint: disable=C0413
import payload  # pylint: disable=C0413
import ras_qemu  # pylint: disable=C0413


class CompilationTest(unittest.TestCase):
    """Only effective compilation settings invalidate a component's build."""

    def test_formatting_does_not_change_key(self) -> None:
        """JSON formatting and key order are not compilation changes."""
        settings = compilation.settings("qemu", "x86_64")
        reordered = json.loads(json.dumps(settings, sort_keys=True, indent=4))
        self.assertEqual(compilation.build_key(settings), compilation.build_key(reordered))

    def test_architecture_option_is_scoped(self) -> None:
        """Changing an x86 feature leaves the ARM build reusable."""
        with open(os.path.join(compilation.CONFIGURATION_DIR, "rasdaemon.json"),
                  encoding="utf-8") as stream:
            changed = json.load(stream)

        changed["architectures"]["x86_64"]["meson"].append("-Dtest-feature=enabled")
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "rasdaemon.json"), "w", encoding="utf-8") as stream:
                json.dump(changed, stream)

            for arch in ("x86_64", "aarch64"):
                before = compilation.build_key(compilation.settings("rasdaemon", arch))
                after = compilation.build_key(compilation.settings("rasdaemon", arch, directory))
                self.assertEqual(before == after, arch == "aarch64")

    def test_common_option_invalidates_both_architectures(self) -> None:
        """A changed configure flag requires both native builds to be updated."""
        for arch in ("x86_64", "aarch64"):
            settings = compilation.settings("qemu", arch)
            changed = copy.deepcopy(settings)
            changed["configure"].append("--enable-debug")
            self.assertNotEqual(compilation.build_key(settings), compilation.build_key(changed))


class PayloadTest(unittest.TestCase):
    """A valid cached payload stages without running any compiler."""

    def setUp(self) -> None:
        # unittest owns cleanup across setUp and the test body.
        temporary = tempfile.TemporaryDirectory()  # pylint: disable=R1732
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.revision = "a" * 40
        values = {"source-revision": self.revision, "architecture": "x86_64",
                  "config.h": "#define HAVE_SQLITE3\n",
                  "rasdaemon-install.tar": "cached installed output",
                  "build-key": compilation.build_key(compilation.settings("rasdaemon", "x86_64"))}

        for name, value in values.items():
            with open(os.path.join(self.directory, name), "w", encoding="utf-8") as stream:
                stream.write(value)

    def test_wrong_provenance_is_rejected(self) -> None:
        """Do not quietly build or run a different SHA, architecture or feature set."""
        for name in ("source-revision", "architecture", "build-key"):
            with self.subTest(name=name):
                path = os.path.join(self.directory, name)
                with open(path, encoding="utf-8") as stream:
                    original = stream.read()
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write("wrong")
                with patch.object(payload.subprocess, "check_output", return_value=self.revision):
                    with self.assertRaises(ValueError):
                        payload.read_prebuilt(self.directory, "x86_64", "source")
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write(original)

    def test_cached_payload_never_compiles(self) -> None:
        """Exercise the real staging path and retain its feature checks."""
        work = os.path.join(self.directory, "work")
        os.mkdir(work)
        machine = ras_qemu.VirtualMachine({}, "x86_64", "tcg", "image", work, "source", 10)
        revision = subprocess.CompletedProcess([], 0, stdout=self.revision)
        with patch.dict(os.environ, RASDAEMON_PREBUILT=self.directory), \
                patch.object(ras_qemu.platform, "machine", return_value="x86_64"), \
                patch.object(payload.subprocess, "check_output", return_value=self.revision), \
                patch.object(ras_qemu.subprocess, "run", return_value=revision), \
                patch.object(ras_qemu.subprocess, "Popen", side_effect=AssertionError("compiled")):
            machine._stage_source()  # pylint: disable=W0212

        archive = os.path.join(work, "payload", "rasdaemon-install.tar")
        with open(archive, encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "cached installed output")
        self.assertEqual(machine.build_checks[0]["name"], "Build HAVE_SQLITE3")
        self.assertEqual(machine.build_checks[0]["status"], "passed")
        self.assertTrue(os.path.isfile(os.path.join(work, "payload", "agent.py")))


if __name__ == "__main__":
    unittest.main()

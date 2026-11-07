#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Reject cached payloads built for different inputs before running a guest."""

import os
import subprocess
import sys
import tempfile
import unittest

from tests.qemu import payload


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PayloadTest(unittest.TestCase):
    """Exercise provenance checks using a real local source repository."""

    def test_accept_matching_payload_and_reject_each_mismatch(self):
        """Every build identity field matters independently."""
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "source")
            os.makedirs(source)

            for args in (("init",), ("config", "user.name", "Test"),
                         ("config", "user.email", "test@example.test"),
                         ("commit", "--allow-empty", "-m", "source")):
                subprocess.run(["git", "-C", source, *args], check=True, capture_output=True)

            revision = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"],
                                               text=True).strip()
            recipe = os.path.join(ROOT, "tests", "qemu", "ci", "build-key.py")
            key = subprocess.check_output([sys.executable, recipe, "rasdaemon"], text=True).strip()
            expected = {"source-revision": revision, "architecture": "aarch64", "build-key": key}

            for name, value in dict(expected, **{"config.h": "#define HAVE_ARM 1\n",
                                                "rasdaemon-install.tar": "compiled"}).items():
                with open(os.path.join(directory, name), "w", encoding="utf-8") as stream:
                    stream.write(value)

            self.assertEqual(payload.read_prebuilt(directory, "aarch64", source),
                             "#define HAVE_ARM 1\n")

            for name, value in expected.items():
                with self.subTest(field=name):
                    with open(os.path.join(directory, name), "w", encoding="utf-8") as stream:
                        stream.write("wrong")

                    with self.assertRaisesRegex(ValueError, name + " mismatch"):
                        payload.read_prebuilt(directory, "aarch64", source)

                    with open(os.path.join(directory, name), "w", encoding="utf-8") as stream:
                        stream.write(value)

            os.unlink(os.path.join(directory, "rasdaemon-install.tar"))

            with self.assertRaisesRegex(ValueError, "archive is missing"):
                payload.read_prebuilt(directory, "aarch64", source)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""Exercise descriptor-driven firmware discovery without a QEMU checkout."""

import json
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.qemu.ci import firmware


class FirmwareTest(unittest.TestCase):
    """Keep upstream filenames and machine versions out of selection policy."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.binary = os.path.join(self.root, "opt", "qemu", "bin", "qemu-system-test")
        self.data = os.path.join(self.root, "opt", "qemu", "share", "qemu")
        os.makedirs(os.path.join(self.data, "firmware"))
        self.code = "/opt/qemu/share/qemu/upstream-code.fd"
        self.variables = "/opt/qemu/share/qemu/renamed-template.fd"
        self.descriptor = {
            "interface-types": ["uefi"],
            "mapping": {"device": "flash",
                        "executable": {"filename": self.code, "format": "raw"},
                        "nvram-template": {"filename": self.variables, "format": "raw"}},
            "targets": [{"architecture": "aarch64", "machines": ["virt-*"]}],
            "features": []}

        for name in (self.code, self.variables):
            with open(os.path.join(self.root, name.lstrip("/")), "wb") as stream:
                stream.write(b"firmware")

        output = "virt  ARM Virtual Machine (alias of virt-99.0)\n"
        listing = subprocess.CompletedProcess([], 0, output)
        patcher = mock.patch.object(firmware.subprocess, "run", return_value=listing)
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def write_descriptor(self, name: str = "60-upstream.json") -> None:
        """Write exactly the metadata that an upstream installation supplies."""
        with open(os.path.join(self.data, "firmware", name), "w", encoding="utf-8") as stream:
            json.dump(self.descriptor, stream)

    def test_uses_descriptor_filenames_and_qemu_machine_alias(self):
        """Follow arbitrary upstream names and the binary's current machine alias."""
        self.write_descriptor()
        selected = firmware.select_firmware(self.root, self.binary, "aarch64", "virt")
        self.assertEqual(selected, {"pairs": [{"code": self.code, "vars": self.variables}]})
        self.run.assert_called_once_with([self.binary, "-machine", "help"],
                                         check=False, capture_output=True, text=True)

    def test_machine_query_reports_runtime_loader_error(self):
        """Keep the actual loader diagnostic when the binary cannot run."""
        self.run.return_value = subprocess.CompletedProcess(
            [], 127, "", "error while loading shared libraries: libexample.so: not found")

        with self.assertRaisesRegex(ValueError, "exited 127:.*libexample.so"):
            firmware.select_firmware(self.root, self.binary, "aarch64", "virt")

    def test_runtime_discovery_updates_manifest_and_preserves_provenance(self):
        """Container-time discovery fills firmware without replacing source SHAs."""
        self.write_descriptor()
        manifest_path = os.path.join(self.root, "images.json")
        manifest = {"architectures": {"aarch64": {"machine": "virt", "firmware": {}}},
                    "kernel": {"commit": "a" * 40}}

        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream)

        with mock.patch.object(firmware.shutil, "which", return_value=self.binary):
            with contextlib.redirect_stdout(io.StringIO()):
                status = firmware.main(["--root", self.root, "--binary", self.binary,
                                        "--arch", "aarch64", "--machine", "virt",
                                        "--manifest", manifest_path])

        self.assertEqual(status, 0)

        with open(manifest_path, encoding="utf-8") as stream:
            actual = json.load(stream)

        manifest["architectures"]["aarch64"]["firmware"] = {
            "pairs": [{"code": self.code, "vars": self.variables}]}
        self.assertEqual(actual, manifest)

    def test_reports_missing_file_from_upstream_descriptor(self):
        """Fail before guest creation when installed firmware is incomplete."""
        self.write_descriptor()
        os.unlink(os.path.join(self.root, self.variables.lstrip("/")))

        with self.assertRaisesRegex(ValueError, "missing firmware files:.*renamed-template.fd"):
            firmware.select_firmware(self.root, self.binary, "aarch64", "virt")

    def test_rejects_other_architecture(self):
        """Do not substitute another architecture's installed firmware."""
        self.write_descriptor()

        with self.assertRaisesRegex(ValueError, "no compatible"):
            firmware.select_firmware(self.root, self.binary, "x86_64", "virt")

    def test_skips_secure_firmware_for_plain_guest(self):
        """Select the plain guest descriptor even when secure firmware sorts first."""
        self.descriptor["features"] = ["secure-boot"]
        self.assertEqual(firmware.firmware_pair(self.descriptor, "aarch64", {"virt-99.0"}), {})
        self.write_descriptor("50-secure.json")
        self.descriptor["features"] = []
        self.write_descriptor()
        selected = firmware.select_firmware(self.root, self.binary, "aarch64", "virt")
        self.assertEqual(selected["pairs"][0]["vars"], self.variables)

    def test_does_not_guess_a_pair_when_descriptors_are_absent(self):
        """Existing blobs alone must not replace upstream's compatibility mapping."""
        with self.assertRaisesRegex(ValueError, "no compatible"):
            firmware.select_firmware(self.root, self.binary, "aarch64", "virt")


if __name__ == "__main__":
    unittest.main()

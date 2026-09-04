#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = pathlib.Path(__file__).parent / "qemu" / "ras_qemu.py"
SPEC = importlib.util.spec_from_file_location("ras_qemu", MODULE_PATH)
ras_qemu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ras_qemu)


class RasQemuTest(unittest.TestCase):
    def descriptor(self):
        return {
            "qemu_binary": "qemu-system-test",
            "machine": "virt",
            "memory_mb": 512,
            "cpus": 1,
            "image": {"filename": "guest.qcow2", "sha256": ""},
        }

    def manifest(self):
        return {
            "format_version": 1,
            "architectures": {"x86_64": self.descriptor()},
        }

    def test_architecture_aliases(self):
        self.assertEqual(ras_qemu.architecture_name("amd64"), "x86_64")
        self.assertEqual(ras_qemu.architecture_name("arm64"), "aarch64")

    def test_manifest_rejects_missing_field(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / "manifest.json"
            manifest = self.manifest()
            del manifest["architectures"]["x86_64"]["machine"]
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ras_qemu.LabError):
                ras_qemu.load_manifest(path)

    def test_probe_reports_missing_image_and_kvm(self):
        with tempfile.TemporaryDirectory() as temporary:
            probe = ras_qemu.CapabilityProbe(self.manifest(), temporary)
            with patch.object(ras_qemu.shutil, "which", return_value=None), \
                 patch.object(ras_qemu.platform, "machine", return_value="x86_64"), \
                 patch.object(ras_qemu.os.path, "exists", return_value=False):
                checks = {check.name: check for check in probe.inspect("x86_64")}
            self.assertFalse(checks["image"].available)
            self.assertFalse(checks["kvm"].available)
            self.assertTrue(checks["tcg"].reason.startswith("QEMU"))

    def test_image_override_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = pathlib.Path(temporary) / "override.qcow2"
            image.write_bytes(b"image")
            probe = ras_qemu.CapabilityProbe(self.manifest(), temporary)
            environment = {"RAS_QEMU_IMAGE_X86_64": str(image)}
            with patch.dict(os.environ, environment, clear=False), \
                 patch.object(ras_qemu.shutil, "which", return_value=None), \
                 patch.object(ras_qemu.platform, "machine", return_value="aarch64"):
                checks = {check.name: check for check in probe.inspect("x86_64")}
            self.assertTrue(checks["image"].available)
            self.assertEqual(checks["image"].value, str(image))

    def test_relative_cache_image_path_is_absolute(self):
        with tempfile.TemporaryDirectory(dir=".") as temporary:
            image = pathlib.Path(temporary) / "guest.qcow2"
            image.write_bytes(b"image")
            probe = ras_qemu.CapabilityProbe(self.manifest(), temporary)
            with patch.object(ras_qemu.shutil, "which", return_value=None), \
                 patch.object(ras_qemu.platform, "machine",
                              return_value="aarch64"):
                checks = {check.name: check
                          for check in probe.inspect("x86_64")}
            self.assertTrue(checks["image"].available)
            self.assertTrue(pathlib.Path(checks["image"].value).is_absolute())

    def test_manifest_checksum_is_enforced_for_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = pathlib.Path(temporary) / "override.qcow2"
            image.write_bytes(b"unexpected")
            manifest = self.manifest()
            manifest["architectures"]["x86_64"]["image"]["sha256"] = "0" * 64
            probe = ras_qemu.CapabilityProbe(manifest, temporary)
            with patch.dict(os.environ,
                            {"RAS_QEMU_IMAGE_X86_64": str(image)}, clear=False), \
                 patch.object(ras_qemu.shutil, "which", return_value=None), \
                 patch.object(ras_qemu.platform, "machine", return_value="aarch64"):
                checks = {check.name: check for check in probe.inspect("x86_64")}
            self.assertFalse(checks["image"].available)
            self.assertEqual(checks["image"].reason, "SHA-256 mismatch")

    def test_accelerator_selection(self):
        checks = [
            ras_qemu.Check("kvm", False, "unavailable"),
            ras_qemu.Check("tcg", True, "available"),
        ]
        self.assertEqual(ras_qemu.choose_accelerator("auto", checks), "tcg")
        with self.assertRaises(ras_qemu.LabError):
            ras_qemu.choose_accelerator("kvm", checks)

    def test_result_outputs_json_and_junit(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = ras_qemu.ResultDocument("x86_64", "tcg", "baseline")
            result.add_test("one", "passed")
            result.add_test("two", "skipped", "not available")
            result.write(pathlib.Path(temporary))
            data = json.loads((pathlib.Path(temporary) / "result.json").read_text(
                encoding="utf-8"))
            self.assertEqual(data["totals"]["passed"], 1)
            self.assertEqual(data["totals"]["skipped"], 1)
            self.assertTrue((pathlib.Path(temporary) / "junit.xml").is_file())

    def test_console_copy_strips_ansi_and_normalizes_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            source = root / "raw-console.log"
            destination = root / "console.log"
            source.write_bytes(
                b"\x1b[0;32m[ OK ]\x1b[0m booted\r\n"
                b"title\x1b]0;ignored\x07 text\r\n"
                b"bell\x07kept\n")
            ras_qemu.copy_text_console(source, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"),
                             "[ OK ] booted\ntitle text\nbellkept\n")

    def test_qemu_command_uses_private_channels_and_no_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            image = root / "image.qcow2"
            image.touch()
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "tcg", image, root,
                pathlib.Path(__file__).parent.parent, 10,
            )
            vm.overlay_path.touch()
            vm.payload_dir.mkdir()
            with patch.object(ras_qemu.shutil, "which", return_value="/usr/bin/qemu"):
                command = vm.command()
            joined = " ".join(command)
            self.assertIn("unix:", joined)
            self.assertIn("fat:ro:", joined)
            self.assertIn("readonly=on", joined)
            self.assertIn("org.rasdaemon.test.0", joined)
            self.assertNotIn("-net", command)
            self.assertNotIn("-netdev", command)

    def test_qemu_command_selects_one_matching_firmware_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            code = root / "code.fd"
            variables = root / "vars.fd"
            code.write_bytes(b"code")
            variables.write_bytes(b"vars")
            descriptor = self.descriptor()
            descriptor["firmware"] = {"pairs": [
                {"code": str(root / "missing-code.fd"),
                 "vars": str(root / "missing-vars.fd")},
                {"code": str(code), "vars": str(variables)},
            ]}
            vm = ras_qemu.VirtualMachine(
                descriptor, "x86_64", "tcg", root / "image.qcow2", root,
                pathlib.Path(__file__).parent.parent, 10,
            )
            vm.overlay_path.touch()
            vm.payload_dir.mkdir()
            with patch.object(ras_qemu.shutil, "which",
                              return_value="/usr/bin/qemu"):
                command = vm.command()
            joined = " ".join(command)
            self.assertIn("file=%s" % code, joined)
            self.assertNotIn("missing-code", joined)
            self.assertEqual((root / "firmware-vars.fd").read_bytes(), b"vars")

    def test_mce_ready_marker_returns_guest_physical_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10, "injection",
            )
            vm.console_path.write_text(
                "ras-qemu-agent: mce-ready physical=0x12345000\n",
                encoding="utf-8")
            self.assertEqual(vm._mce_ready_address(), 0x12345000)

    def test_scenario_markers_are_parsed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10, "injection",
            )
            vm.console_path.write_text(
                "ras-qemu-agent: mce-ready physical=0x12345000\n"
                "ras-qemu-agent: aer-ready device=ras-aer error=BAD_DLLP\n",
                encoding="utf-8")
            self.assertEqual(vm._ready_markers(), {
                "mce": {"physical": "0x12345000"},
                "aer": {"device": "ras-aer", "error": "BAD_DLLP"},
            })

    def test_injection_command_adds_aer_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10, "injection",
            )
            vm.overlay_path.touch()
            vm.payload_dir.mkdir()
            with patch.object(ras_qemu.shutil, "which",
                              return_value="/usr/bin/qemu"):
                command = vm.command()
            joined = " ".join(command)
            self.assertIn("pcie-root-port,id=ras-aer-root", joined)
            self.assertIn("virtio-rng-pci,id=ras-aer", joined)
            self.assertIn("aer=on", joined)

    def test_injection_command_adds_blkdebug_disk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10, "injection")
            vm.overlay_path.touch()
            vm.payload_dir.mkdir()
            with patch.object(ras_qemu.shutil, "which",
                              return_value="/usr/bin/qemu"):
                command = vm.command()
            joined = " ".join(command)
            self.assertIn("blkdebug:", joined)
            self.assertIn("serial=ras-block-error", joined)

    def test_mce_monitor_command_targets_guest_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10, "injection",
            )
            fake_qmp = unittest.mock.MagicMock()
            fake_qmp.__enter__.return_value = fake_qmp
            fake_qmp.has_command.return_value = True
            fake_qmp.execute.return_value = ""
            fake_qmp.transcript = []
            with patch.object(ras_qemu, "QmpClient", return_value=fake_qmp):
                evidence = vm._inject_mce(0x12345000)
            command = fake_qmp.execute.call_args.args[1]["command-line"]
            self.assertIn("mce 0 0", command)
            self.assertIn("0x9c00000000000090", command)
            self.assertIn("0x12345000", command)
            self.assertEqual(evidence["physical_address"], "0x12345000")

    def test_aer_monitor_command_targets_dedicated_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10, "injection",
            )
            fake_qmp = unittest.mock.MagicMock()
            fake_qmp.__enter__.return_value = fake_qmp
            fake_qmp.has_command.return_value = True
            fake_qmp.execute.return_value = "OK id: ras-aer root bus: /\n"
            fake_qmp.transcript = []
            with patch.object(ras_qemu, "QmpClient", return_value=fake_qmp):
                evidence = vm._inject_aer()
            command = fake_qmp.execute.call_args.args[1]["command-line"]
            self.assertEqual(command,
                             "pcie_aer_inject_error ras-aer BAD_DLLP")
            self.assertEqual(evidence["error"], "BAD_DLLP")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
import importlib.util
import json
import os
import pathlib
import re
import struct
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = pathlib.Path(__file__).parent / "qemu" / "ras_qemu.py"
SPEC = importlib.util.spec_from_file_location("ras_qemu", MODULE_PATH)
ras_qemu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ras_qemu)

AGENT_PATH = pathlib.Path(__file__).parent / "qemu" / "guest" / "agent.py"
AGENT_SPEC = importlib.util.spec_from_file_location("agent", AGENT_PATH)
agent = importlib.util.module_from_spec(AGENT_SPEC)
AGENT_SPEC.loader.exec_module(agent)


class RasQemuTest(unittest.TestCase):
    @staticmethod
    def descriptor():
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

    def test_result_explains_implicit_component_skips(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            result = ras_qemu.ResultDocument("x86_64", "tcg", "injection")
            result.add_test("prerequisites", "passed")
            result.add_test("database-report", "passed")
            result.data["tests"][-1].update(kernel="SKIP", rasdaemon="PASS")
            result.add_test("Enable Kernel CONFIG_ACPI_APEI_GHES config", "passed",
                            "Kernel option is y as requested")
            result.data["tests"][-1].update(kernel="PASS", rasdaemon="N/A")
            result.add_test("injection", "failed", "kernel event was not observed")
            result.data["tests"][-1].update(kernel="FAIL", rasdaemon="SKIP")

            result.write(root)

            data = json.loads((root / "result.json").read_text(encoding="utf-8"))
            reasons = {test["name"]: test["reason"] for test in data["tests"]}
            self.assertEqual(data["component_totals"], {
                "kernel": {"passed": 1, "failed": 1, "skipped": 2,
                           "not_applicable": 0},
                "rasdaemon": {"passed": 1, "failed": 0, "skipped": 2,
                              "not_applicable": 1},
            })
            self.assertIn("did not exercise kernel RAS handling or rasdaemon",
                          reasons["prerequisites"])
            self.assertIn("rasdaemon-only check", reasons["database-report"])
            self.assertIn("rasdaemon was not evaluated",
                          reasons["injection"])
            summary = (root / "summary.md").read_text(encoding="utf-8")
            self.assertIn("| Kernel | 1 | 1 | 2 | 0 |", summary)
            self.assertIn("| rasdaemon | 1 | 0 | 2 | 1 |", summary)
            self.assertNotIn("rasdaemon is not involved", summary)
            self.assertIn(reasons["prerequisites"], summary)
            self.assertIn(reasons["database-report"], summary)

        # The CXL AER setup must change only the selected component mask bit.
        scenarios = {
            "cxl-aer-ce": ("cxl/cxl_aer_correctable_error",
                           "cxl_aer_ce_event", 0x1090, 0x7f, 1),
            "cxl-aer-ue": ("cxl/cxl_aer_uncorrectable_error",
                           "cxl_aer_ue_event", 0x1084, 0x1cfff, 4),
        }

        for name, values in scenarios.items():
            event, table, offset, initial, bit = values

            with self.subTest(name=name):
                results = agent.Results()
                scenario = {"name": name, "event": event, "table": table}
                recorded = agent.RecordedScenario(results, "/build", {}, scenario)
                component = bytearray(8192)
                struct.pack_into("<I", component, offset, initial)

                with tempfile.NamedTemporaryFile() as resource:
                    resource.write(component)
                    resource.flush()

                    with patch.object(agent.glob, "glob",
                                      return_value=["/sys/bus/cxl/devices/mem0"]), \
                         patch.object(agent.os.path, "realpath",
                                      return_value="/sys/devices/pci0000:34/0000:35:00.0/mem0"), \
                         patch.object(agent.os.path, "join",
                                      return_value=resource.name):
                        recorded.unmask_cxl_aer_error()

                    resource.seek(offset)
                    after = struct.unpack("<I", resource.read(4))[0]
                    self.assertEqual(after, initial & ~(1 << bit))

                mask = recorded.evidence["cxl_aer_component_mask"]
                self.assertEqual(mask["offset"], offset)
                self.assertEqual(mask["before"], initial)
                self.assertEqual(mask["after"], initial & ~(1 << bit))

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
                "ras-qemu-agent: mce-ready physical=0x12345000 bank=2\n",
                encoding="utf-8")
            self.assertEqual(vm.mce_ready_target(),
                             {"physical": 0x12345000, "bank": 2})

    def test_guest_boot_marker_is_required_before_injection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            vm = ras_qemu.VirtualMachine(
                self.descriptor(), "x86_64", "kvm", root / "image.qcow2",
                root, pathlib.Path(__file__).parent.parent, 10,
                "injection",
            )
            vm.console_path.write_text(
                "ras-qemu-agent: payload passed\n", encoding="utf-8")
            self.assertTrue(vm.guest_booted())

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
            self.assertEqual(vm.ready_markers(), {
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
            self.assertIn(
                "tcp:127.0.0.1:%d,server=on,wait=off" %
                ras_qemu.GHES_QMP_PORT,
                joined,
            )

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
            # A pxb-cxl bus changes QEMU's implicit PCI bus selection.
            for index, value in enumerate(command):
                if value == "-device" and command[index + 1].startswith("virtio-"):
                    self.assertIn("bus=", command[index + 1])
            self.assertIn("cxl-type3,id=ras-cxl", joined)
            self.assertIn("ras=on,cxl=on", joined)
            self.assertNotIn("if=virtio", joined)

    def test_pci_bus_helper_uses_supported_arguments(self):
        """Do not pass ARM-only severity arguments to the PCI bus helper."""
        with open("tests/qemu/scenarios.json", encoding="utf-8") as stream:
            scenarios = json.load(stream)

        scenario = next(item for item in scenarios if item["name"] == "ghes-pci-bus")

        self.assertEqual(scenario["helper"], ["pci-bus"])


class InjectionEvidenceTest(unittest.TestCase):
    """Check safe producers and evidence retained after injection failures."""

    descriptor = staticmethod(RasQemuTest.descriptor)

    def test_cxl_pcie_internal_error_masks(self) -> None:
        """Unmask only the PCIe carrier bit and reject ignored writes."""
        scenarios = [("cxl-aer-ce", "ECAP_AER+14.L", 0xe000, 0x4000),
                     ("cxl-aer-ue", "ECAP_AER+08.L", 0x2400000, 0x400000)]

        for name, register, before, mask in scenarios:
            for ignored in (False, True):
                with self.subTest(name=name, ignored=ignored):
                    scenario = {"name": name, "event": "cxl/event", "table": "event"}
                    recorded = agent.RecordedScenario(unittest.mock.Mock(), "/build", {}, scenario)
                    recorded.evidence["cxl_aer_component_mask"] = {"bdf": "0000:35:00.0"}
                    after = before if ignored else before & ~mask
                    responses = [unittest.mock.Mock(returncode=0, stdout=f"{before:08x}\n"),
                                 unittest.mock.Mock(returncode=0, stdout=""),
                                 unittest.mock.Mock(returncode=0, stdout=f"{after:08x}\n")]

                    with patch.object(agent, "run", side_effect=responses) as command:
                        if ignored:
                            with self.assertRaisesRegex(RuntimeError, "selected bit"):
                                recorded.unmask_cxl_pcie_error()
                        else:
                            recorded.unmask_cxl_pcie_error()

                    self.assertEqual(command.call_args_list[1].args[0],
                                     ["setpci", "-s", "0000:35:00.0",
                                      f"{register}=00000000:{mask:08x}"])
                    self.assertEqual(recorded.evidence["cxl_aer_pcie_mask"]["after"], after)

    def test_cxl_aer_diagnostics_are_limited_to_aer_scenarios(self) -> None:
        """Avoid running PCI diagnostics for unrelated recorded scenarios."""
        scenario = {"name": "cxl-media", "event": "cxl/cxl_general_media",
                    "table": "cxl_general_media_event"}
        recorded = agent.RecordedScenario(unittest.mock.Mock(), "/build", {}, scenario)

        with patch.object(agent.subprocess, "run") as command:
            recorded.capture_cxl_aer_state("before")
            recorded.unmask_cxl_pcie_error()

        command.assert_not_called()
        self.assertNotIn("cxl_aer_before", recorded.evidence)

    def test_no_emission_failure_identifies_kernel_stage(self) -> None:
        """Describe an empty observer and database as a kernel failure."""
        scenario = {"name": "cxl-aer-ce", "event": "cxl/cxl_aer_correctable_error",
                    "table": "cxl_aer_ce_event"}
        results = unittest.mock.Mock()
        recorded = agent.RecordedScenario(results, "/build", {}, scenario)

        with patch("builtins.open", unittest.mock.mock_open()), \
             patch.object(recorded, "start"), patch.object(recorded, "ready"), \
             patch.object(recorded, "inject_page"), \
             patch.object(recorded, "wait_record", side_effect=RuntimeError(
                 "injection produced no matching cxl_aer_ce_event row")), \
             patch.object(recorded, "cleanup"):
            recorded.execute()

        result = results.add.call_args
        self.assertEqual(result.args[2],
                         "injection produced no kernel event or matching database row")
        self.assertEqual(result.kwargs["kernel"], "FAIL")
        self.assertEqual(result.kwargs["rasdaemon"], "SKIP")

    def test_incomplete_ready_marker_does_not_trigger_injection(self) -> None:
        """The serial console can be read while a line is still being written."""
        with tempfile.TemporaryDirectory() as temporary:
            machine = ras_qemu.VirtualMachine(self.descriptor(), "x86_64", "tcg",
                                             os.path.join(temporary, "guest"), temporary,
                                             temporary, 30, "injection")

            with open(machine.console_path, "w", encoding="utf-8") as stream:
                stream.write("ras-qemu-agent: ghes-aer-ready bdf=0000:")

            self.assertNotIn("ghes-aer", machine.ready_markers())

            with open(machine.console_path, "a", encoding="utf-8") as stream:
                stream.write("01:00.0\n")

            self.assertEqual(machine.ready_markers()["ghes-aer"]["bdf"], "0000:01:00.0")

    def test_raw_memory_cper_is_corrected_and_has_no_valid_address(self) -> None:
        """Verify CPER severity, section lengths and safe validation bits."""
        with tempfile.TemporaryDirectory() as temporary:
            machine = ras_qemu.VirtualMachine(self.descriptor(), "x86_64", "tcg",
                                         os.path.join(temporary, "guest"), temporary,
                                         temporary, 30, "injection")
            scenario = next(item for item in machine.scenarios if item["name"] == "ghes-memory")
            completed = unittest.mock.Mock(returncode=0, stdout="injected")
            with patch.object(ras_qemu.subprocess, "run", return_value=completed):
                machine.inject_scenario(scenario)
            raw = machine.injection_evidence["ghes-memory"]["raw_cper"]
            pattern = r"^    [0-9a-f]{8}  ([0-9a-f ]+?)  [.]"
            data = bytes.fromhex(" ".join(re.findall(pattern, raw, re.MULTILINE)))
            # The raw helper receives one 20-byte GESB, 72-byte GEDE,
            # and an 80-byte memory section with only error type valid.
            self.assertEqual(len(data), 172)
            self.assertEqual(struct.unpack_from("<IIIII", data), (0x12, 0, 0, 152, 2))
            self.assertEqual(struct.unpack_from("<I", data, 36)[0], 2)
            self.assertEqual(struct.unpack_from("<Q", data, 92)[0], 1 << 14)
            self.assertEqual(data[164], 2)

    def test_overflow_resumes_guest_when_injection_fails(self) -> None:
        """A rejected event must not leave the VM paused."""
        with tempfile.TemporaryDirectory() as temporary:
            machine = ras_qemu.VirtualMachine(self.descriptor(), "x86_64", "tcg",
                                         os.path.join(temporary, "guest"), temporary,
                                         temporary, 30, "injection")
            scenario = next(item for item in machine.scenarios if item["name"] == "cxl-overflow")
            qmp = unittest.mock.MagicMock()
            qmp.__enter__.return_value = qmp
            qmp.execute.side_effect = [None, ras_qemu.LabError("injection failed"), None]
            with patch.object(ras_qemu, "QmpClient", return_value=qmp):
                with self.assertRaises(ras_qemu.LabError):
                    machine.inject_scenario(scenario)
            self.assertEqual([call.args[0] for call in qmp.execute.call_args_list],
                             ["stop", "cxl-inject-general-media-event", "cont"])

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
                evidence = vm.inject_mce(0x12345000, 2)
            command = fake_qmp.execute.call_args.args[1]["command-line"]
            self.assertIn("mce 0 2", command)
            self.assertIn("0x9c00000000000090", command)
            self.assertIn("0x12345000", command)
            self.assertEqual(evidence["physical_address"], "0x12345000")
            self.assertEqual(evidence["bank"], 2)

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
                evidence = vm.inject_aer()
            command = fake_qmp.execute.call_args.args[1]["command-line"]
            self.assertEqual(command,
                             "pcie_aer_inject_error ras-aer BAD_DLLP")
            self.assertEqual(evidence["error_type"], "BAD_DLLP")
            self.assertNotIn("injection_error", evidence)

    def test_only_explicit_injection_failures_override_verdicts(self):
        tests = [
            {"name": "aer-native", "status": "passed", "kernel": "PASS",
             "rasdaemon": "PASS", "reason": ""},
            {"name": "ghes-memory", "status": "passed", "kernel": "PASS",
             "rasdaemon": "PASS", "reason": ""},
        ]
        injections = {
            "aer": {"error_type": "BAD_DLLP"},
            "ghes-memory": {"injection_error": "helper crashed"},
        }

        ras_qemu.apply_injection_failures(tests, injections)

        self.assertEqual(tests[0]["status"], "passed")
        self.assertEqual(tests[0]["kernel"], "PASS")
        self.assertEqual(tests[1], {
            "name": "ghes-memory", "status": "failed", "kernel": "SKIP",
            "rasdaemon": "SKIP", "reason": "Injection failed: helper crashed",
        })


if __name__ == "__main__":
    unittest.main()

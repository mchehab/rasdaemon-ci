# SPDX-License-Identifier: GPL-2.0-only
"""Host-only regressions for the HiSilicon corpus; no VM or injections."""

import importlib.util
import os
import struct
import unittest


MODULE_PATH = os.path.join(os.path.dirname(__file__), "qemu", "hisi.py")
SPEC = importlib.util.spec_from_file_location("hisi_fixtures", MODULE_PATH)
hisi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hisi)


class HisiFixtureTest(unittest.TestCase):
    """Check wire offsets and corpus breadth independently of the guest decoder."""

    def test_all_registered_guids_have_a_corpus(self):
        scenarios = hisi.scenarios()
        self.assertEqual(len(scenarios), 4)
        guids = {case["cper"]["guid"] for scenario in scenarios for case in scenario["cases"]}
        self.assertEqual(guids, set(hisi.GUIDS.values()))
        self.assertEqual(len({scenario["name"] for scenario in scenarios}), 4)

    def test_common_wire_fields(self):
        case = hisi.common_case()
        payload = bytes.fromhex(case["cper"]["payload"])
        self.assertEqual(len(payload), 44)
        self.assertEqual(struct.unpack_from("<I", payload)[0], 0x1fff)
        self.assertEqual(struct.unpack_from("<BBHB", payload, 16), (3, 4, 0x123, 0x56))
        self.assertEqual(struct.unpack_from("<IIII", payload, 28),
                         (12, 0x12345678, 0xabcdef01, 0))
        self.assertEqual(case["expected"]["module_id"], "DDRC")

    def test_common_covers_all_module_and_soc_names(self):
        cases = hisi.scenarios()[0]["cases"]
        modules = {case["expected"]["module_id"] for case in cases}
        self.assertEqual(modules, set(hisi.COMMON_MODULES) | {None, "unknown"})
        self.assertTrue({0, 1, 2, 255}.issubset({case["expected"]["soc_id"] for case in cases}))

    def test_oem_wire_layouts(self):
        first = bytes.fromhex(hisi.oem_case("type1")["cper"]["payload"])
        second = bytes.fromhex(hisi.oem_case("type2")["cper"]["payload"])
        self.assertEqual(len(first), 40)
        self.assertEqual(len(second), 60)
        self.assertEqual(struct.unpack_from("<Q", first, 32)[0], 0x123456789abcdef0)
        self.assertEqual(struct.unpack_from("<I", second, 56)[0], 0x5678000b)

    def test_header_only_register_dump_is_empty(self):
        for kind in ("type1", "type2"):
            case = hisi.oem_case(kind, valid=1)
            self.assertEqual(case["expected"]["regs_dump"], "")
            self.assertIsNone(case["expected"]["module_id"])
        self.assertEqual(hisi.pcie_case(valid=1)["expected"]["regs_dump"], "")

    def test_pcie_last_register_and_high_validity_bits(self):
        case = hisi.pcie_case(valid=1 << 41)
        payload = bytes.fromhex(case["cper"]["payload"])
        self.assertEqual(len(payload), 152)
        self.assertEqual(struct.unpack_from("<Q", payload)[0], 1 << 41)
        self.assertEqual(struct.unpack_from("<I", payload, 148)[0], 0xabcd0020)
        self.assertEqual(case["expected"]["regs_dump"], "ERR_MISC_32=0xabcd0020")

    def test_no_duplicate_payloads_within_a_family(self):
        for scenario in hisi.scenarios():
            payloads = [case["cper"]["payload"] for case in scenario["cases"]]
            self.assertEqual(len(payloads), len(set(payloads)), scenario["name"])

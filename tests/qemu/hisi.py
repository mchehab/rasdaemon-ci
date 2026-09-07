# SPDX-License-Identifier: GPL-2.0-only
"""Deterministic CPER corpus for all four HiSilicon decoder registrations.

Layouts and names follow non-standard-hisilicon.c and non-standard-hisi_hip08.c.
Fixtures are independent of the decoder at runtime, so decoder regressions
cannot silently change the expected values. Each family shares one daemon.
"""

import struct


COMMON_MODULES = (
    "MN PLL SLLC AA SIOE POE CPA DISP GIC ITS AVSBUS CS PPU SMMU PA HLLC DDRC "
    "L3TAG L3DATA PCS HHA PCIe_Local SAS SATA NIC RoCE USB ZIP HPRE SEC RDE MEE "
    "L4D Tsensor ROH BTC HILINK STARS SDMA UC HBMC PMC SCHE ASMB_DFS ASMB_NTU UB "
    "UMMU PCU UCMI DJTAGM CFGBUS MPU CRG ACG3 DCIP UMAU UPA AXI_MSTR_OOO RBIST LC300"
).split()
COMMON_MODULES[21] = "PCIe Local"

TYPE1_MODULES = {
    0: ("MN", []),
    1: ("PLL", "TB_PLL0 TB_PLL1 TB_PLL2 TB_PLL3 TA_PLL0 TA_PLL1 TA_PLL2 TA_PLL3 "
        "NIMBUS_PLL0 NIMBUS_PLL1 NIMBUS_PLL2 NIMBUS_PLL3 NIMBUS_PLL4".split()),
    2: ("SLLC", "TB_SLLC0 TB_SLLC1 TB_SLLC2 TA_SLLC0 TA_SLLC1 TA_SLLC2 "
        "NIMBUS_SLLC0 NIMBUS_SLLC1".split()),
    3: ("AA", []),
    4: ("SIOE", "TB_SIOE0 TB_SIOE1 TB_SIOE2 TB_SIOE3 TA_SIOE0 TA_SIOE1 TA_SIOE2 "
        "TA_SIOE3 NIMBUS_SIOE0 NIMBUS_SIOE1".split()),
    5: ("POE", ["TB_POE", "TA_POE"]),
    8: ("DISP", "TB_PERI_DISP TB_POE_DISP TB_GIC_DISP TA_PERI_DISP TA_POE_DISP "
        "TA_GIC_DISP HAC_DISP PCIE_DISP IO_MGMT_DISP NETWORK_DISP".split()),
    9: ("LPC", []), 13: ("GIC", []), 14: ("RDE", []),
    15: ("SAS", ["SAS0", "SAS1"]), 16: ("SATA", []), 17: ("USB", []),
}
TYPE2_MODULES = {
    0: ("SMMU", "HAC_SMMU PCIE_SMMU MGMT_SMMU NIC_SMMU".split()),
    1: ("HHA", "TB_HHA0 TB_HHA1 TA_HHA0 TA_HHA1".split()),
    2: ("PA", []), 3: ("HLLC", "HLLC0 HLLC1 HLLC2".split()),
    4: ("DDRC", "TB_DDRC0 TB_DDRC1 TB_DDRC2 TB_DDRC3 "
        "TA_DDRC0 TA_DDRC1 TA_DDRC2 TA_DDRC3".split()),
    5: ("L3TAG", [f"{side}_PARTITION{index}" for side in ("TB", "TA") for index in range(8)]),
    6: ("L3DATA", [f"{side}_BANK{index}" for side in ("TB", "TA") for index in range(4)]),
}
SEVERITIES = {0: "recoverable", 1: "fatal", 2: "corrected", 3: "none", 255: "unknown"}
GUIDS = {
    "common": "c8b328a8-9917-4af6-9a13-2e08ab2e7586",
    "type1": "1f8161e1-55d6-41e6-bd10-7afd1dc5f7c5",
    "type2": "45534ea6-ce23-4115-8535-e07ab3aef91d",
    "pcie": "b2889fc9-e7d7-4f9d-a867-af42e98be772",
}


def common_case(module: int = 16, severity: int = 2, valid: int = 0x1fff,
                soc: int = 2) -> dict:
    """Common section: every header field, BDF, and a nonempty register array."""
    data = bytearray(44)
    struct.pack_into("<I10BH", data, 0, valid, 2, soc, 3, 4, 5, 6, module, 7, 8, 9, 0x1234)
    struct.pack_into("<BBHB", data, 16, 3, 4, 0x123, 0x56)
    data[24] = severity
    struct.pack_into("<IIII", data, 28, 12, 0x12345678, 0xabcdef01, 0)
    module_name = COMMON_MODULES[module] if module < len(COMMON_MODULES) else "unknown"
    values = {"soc_id": soc, "socket_id": 3, "totem_id": 4, "nimbus_id": 5,
              "sub_system_id": 6, "module_id": module_name, "sub_module_id": 7,
              "core_id": 8, "port_id": 9, "err_type": 0x1234,
              "pcie_info": "0123:56:04.3", "err_severity": SEVERITIES[severity]}
    expected = {name: value if valid & (1 << bit) else None
                for bit, (name, value) in enumerate(values.items())}
    expected["version"] = 2
    fragments = ["reg00=0x12345678", "reg01=0xabcdef01", "reg02=0x00000000"]
    if not valid & (1 << 12):
        expected["regs_dump"] = ""
        fragments = []

    return {"cper": {"guid": GUIDS["common"], "payload": data.hex()},
            "expected": expected, "expected_contains": {"regs_dump": fragments}}


def oem_case(kind: str, module: int = 1, submodule: int = 0,
             severity: int = 2, valid: int = 0xfff) -> dict:
    """HIP08 OEM sections: all module names and register validity fields."""
    modules = TYPE1_MODULES if kind == "type1" else TYPE2_MODULES
    name, submodules = modules.get(module, ("unknown", []))
    subname = (submodules[submodule] if submodule < len(submodules) else "unknown"
               ) if submodules else name
    data = bytearray(40 if kind == "type1" else 60)
    struct.pack_into("<I8B", data, 0, valid, 1, 17, 2, 3, module, submodule, severity, 0)
    values = {"soc_id": 17, "socket_id": 2, "nimbus_id": 3,
              "module_id": name, "sub_module_id": subname, "err_severity": SEVERITIES[severity]}
    expected = {key: value if valid & (1 << bit) else None
                for bit, (key, value) in enumerate(values.items())}
    expected["version"] = 1
    registers = []

    if kind == "type1":
        for index in range(5):
            value = 0x12340000 + index
            struct.pack_into("<I", data, 12 + 4 * index, value)
            if valid & (1 << (6 + index)):
                registers.append(f"ERR_MISC{index}=0x{value:x}")

        struct.pack_into("<Q", data, 32, 0x123456789abcdef0)
        if valid & (1 << 11):
            registers.append("ERR_ADDR=0x123456789abcdef0")
    else:
        for index, register in enumerate(("FR", "CTRL", "STATUS", "ADDR", "MISC0", "MISC1")):
            for half in range(2):
                value = 0x56780000 + index * 2 + half
                struct.pack_into("<I", data, 12 + 8 * index + 4 * half, value)
                if valid & (1 << (6 + index)):
                    registers.append(f"ERR_{register}_{half}=0x{value:x}")

    expected["regs_dump"] = " ".join(registers)
    return {"cper": {"guid": GUIDS[kind], "payload": data.hex()}, "expected": expected}


def pcie_case(submodule: int = 0, severity: int = 2,
              valid: int = (1 << 42) - 1) -> dict:
    """HIP08 PCIe local section, including all 33 register-validity bits."""
    data = bytearray(152)
    struct.pack_into("<Q8BH", data, 0, valid, 1, 17, 2, 3, submodule, 4, 5, severity, 0x1234)
    names = ["AP_Layer", "TL_Layer", "MAC_Layer", "DL_Layer", "SDI_Layer"]
    name = names[submodule] if submodule < len(names) else "unknown"
    fields = {"soc_id": (1, 17), "socket_id": (2, 2), "nimbus_id": (3, 3),
              "sub_module_id": (4, name), "core_id": (5, 4), "port_id": (6, 5),
              "err_type": (7, 0x1234), "err_severity": (8, SEVERITIES[severity])}
    expected = {key: value if valid & (1 << bit) else None
                for key, (bit, value) in fields.items()}
    expected["version"] = 1
    registers = []

    for index in range(33):
        value = 0xabcd0000 + index
        struct.pack_into("<I", data, 20 + index * 4, value)
        if valid & (1 << (9 + index)):
            registers.append(f"ERR_MISC_{index}=0x{value:x}")

    expected["regs_dump"] = " ".join(registers)
    return {"cper": {"guid": GUIDS["pcie"], "payload": data.hex()}, "expected": expected}


def scenarios() -> list[dict]:
    """Cover every decoder, named module/submodule, severity and validity field."""
    common = [common_case(module=module) for module in range(len(COMMON_MODULES))]
    common += [common_case(module=255), common_case(soc=0), common_case(soc=1),
               common_case(soc=255)]
    common += [common_case(severity=severity) for severity in (0, 1, 3, 255)]
    common += [common_case(valid=1 << bit) for bit in range(13)]
    common.append(common_case(valid=0))
    corpus = {"common": common}

    for kind, modules in (("type1", TYPE1_MODULES), ("type2", TYPE2_MODULES)):
        cases = []
        for module, (_name, submodules) in modules.items():
            for submodule in range(max(1, len(submodules))):
                cases.append(oem_case(kind, module, submodule))
            if submodules:
                cases.append(oem_case(kind, module, len(submodules)))

        cases.append(oem_case(kind, 255, 255))
        cases += [oem_case(kind, severity=severity) for severity in (0, 1, 3, 255)]
        cases += [oem_case(kind, valid=1 << bit) for bit in range(12)]
        corpus[kind] = cases

    pcie = [pcie_case(submodule=submodule) for submodule in (0, 1, 2, 3, 4, 255)]
    pcie += [pcie_case(severity=severity) for severity in (0, 1, 3, 255)]
    pcie += [pcie_case(valid=1 << bit) for bit in range(42)]
    corpus["pcie"] = pcie
    tables = {"common": "hisi_common_section_v2", "type1": "hip08_oem_type1_event_v2",
              "type2": "hip08_oem_type2_event_v2", "pcie": "hip08_pcie_local_event_v2"}
    return [{"name": "vendor-hisilicon-" + kind, "event": "ras/non_standard_event",
             "table": tables[kind], "cases": cases} for kind, cases in corpus.items()]

# SPDX-License-Identifier: GPL-2.0-only
"""Feature ownership and strict functional coverage, independent of setup checks."""

import json
import html
import os
import re


# One owner per feature. Multiple checks cover distinct paths of that feature.
FEATURES = {
    "abrt-report": ("aarch64", ["consumer-abrt-report"]),
    "aer": ("x86_64", ["aer-native", "ghes-aer"]),
    "amp-ns-decode": ("aarch64", ["vendor-ampere"]),
    "ampere-oem-sel": ("aarch64", ["consumer-arm-sel", "consumer-ampere-oem-sel"]),
    "arm": ("aarch64", ["ghes-arm"]),
    "arm-vendor-data": ("aarch64", ["arm-vendor-data"]),
    "bmc-generic": ("x86_64", ["consumer-bmc-generic"]),
    "cpu-fault-isolation": ("aarch64", ["consumer-cpu-fault-isolation"]),
    "cxl": ("x86_64", ["cxl-media", "cxl-dram", "cxl-module", "cxl-poison",
                         "cxl-overflow", "cxl-aer-ce", "cxl-aer-ue",
                         "cxl-generic", "cxl-memory-sparing"]),
    "db": ("aarch64", ["consumer-database-report"]),
    "devlink": ("aarch64", ["devlink-health", "net-xmit-timeout"]),
    "diskerror": ("aarch64", ["block-io-native"]),
    "erst": ("x86_64", ["erst-persistence"]),
    "extlog": ("x86_64", ["extlog-memory"]),
    "hisi-ns-decode": ("aarch64", ["vendor-hisilicon-common", "vendor-hisilicon-type1",
                                  "vendor-hisilicon-type2", "vendor-hisilicon-pcie"]),
    "ipmi-bmc": ("x86_64", ["consumer-bmc-generic"]),
    "jaguar-ns-decode": ("aarch64", ["vendor-jaguar"]),
    "mc-event": ("aarch64", ["ghes-memory"]),
    "mce": ("x86_64", ["mce-hardware-first"]),
    "memory-ce-pfa": ("aarch64", ["consumer-memory-ce-pfa"]),
    "memory-failure": ("aarch64", ["memory-failure"]),
    "memory-row-ce-pfa": ("aarch64", ["consumer-memory-row-ce-pfa"]),
    "mysql": ("aarch64", ["consumer-mysql"]),
    "non-standard": ("aarch64", ["ghes-pci-bus", "ghes-unknown"]),
    "nvidia-ns-decode": ("aarch64", ["vendor-nvidia"]),
    "openbmc-unified-sel": ("aarch64", ["consumer-arm-sel", "consumer-openbmc-unified-sel"]),
    "page-isolation": ("aarch64", ["consumer-memory-ce-pfa",
                                    "consumer-memory-row-ce-pfa"]),
    "pcie-edpc": ("x86_64", ["consumer-pcie-edpc"]),
    "poison-page-stat": ("aarch64", ["consumer-poison-page-stat"]),
    "postgresql": ("aarch64", ["consumer-postgresql"]),
    "reri": ("aarch64", ["reri-event"]),
    "signal": ("aarch64", ["memory-sigbus"]),
    "sqlite3": ("aarch64", ["consumer-sqlite3"]),
    "trigger": ("aarch64", ["consumer-trigger"]),
    "yitian-ns-decode": ("aarch64", ["vendor-yitian"]),
}

# Explicit platform limitations, not a fallback for missing or broken tests.
# Remove an entry when the corresponding producer/device becomes supported.
UNSUPPORTED = {
    "reri": "The x86_64/aarch64 stack has no RERI producer; RISC-V support is out of scope",
    "pcie-edpc": "The current QEMU test topology has no DPC-capable PCIe port",
}

UNIMPLEMENTED_CHECKS = {
    "cxl-generic": "No generic CXL event producer has been implemented in the harness",
}


def scenario_arch(name: str) -> str:
    """Return the unique architecture responsible for a scenario."""
    owners = {arch for arch, checks in FEATURES.values() if name in checks}
    if len(owners) != 1:
        raise ValueError(f"Scenario {name} needs exactly one architecture owner: {owners}")

    return owners.pop()


def feature_inventory(source: str) -> list[str]:
    """Discover new Meson features so unassigned additions cannot silently pass."""
    with open(os.path.join(source, "meson_options.txt"), encoding="utf-8") as stream:
        options = stream.read()

    names = set(re.findall(r"option\('([^']+)',\s*type\s*:\s*'feature'", options))
    with open(os.path.join(source, "meson.build"), encoding="utf-8") as stream:
        build = stream.read()

    # Internal feature switches include the shared consumers selected by options.
    internal = set(re.findall(r"^enable_(\w+) = false$", build, re.MULTILINE))
    names.update(name.replace("_", "-") for name in internal)
    names.add("mc-event")
    names.add("trigger")
    return sorted(names)


def feature_results(arch: str, tests: list[dict], inventory: list[str]) -> list[dict]:
    """Judge implemented checks; list implementation gaps outside regression totals."""
    grouped: dict[str, list[dict]] = {}
    for test in tests:
        grouped.setdefault(test["name"], []).append(test)

    rows = []
    for name in inventory:
        owner, checks = FEATURES.get(name, ("aarch64", []))
        if owner != arch:
            continue

        if name in UNSUPPORTED:
            rows.append({"feature": name, "arch": owner, "PASS": 0, "FAIL": 0,
                         "N/A": 1, "reason": UNSUPPORTED[name], "checks": [],
                         "untested": [{"check": name, "reason": UNSUPPORTED[name]}]})
            continue

        untested = [{"check": check, "reason": UNIMPLEMENTED_CHECKS[check]}
                    for check in checks if check in UNIMPLEMENTED_CHECKS]
        checks = [check for check in checks if check not in UNIMPLEMENTED_CHECKS]
        if not checks:
            reason = "No functional test implementation is assigned to this feature"
            rows.append({"feature": name, "arch": owner, "PASS": 0, "FAIL": 0,
                         "N/A": 1, "reason": reason, "checks": [],
                         "untested": untested or [{"check": name, "reason": reason}]})
            continue

        failed = [check for check in checks if len(grouped.get(check, [])) != 1 or
                  grouped[check][0]["status"] != "passed"]
        passed = bool(checks) and not failed
        reason = "All implemented functional checks passed" if passed else (
            "Missing, failed or skipped checks: " + ", ".join(failed)
            if checks else "Feature has no functional coverage assignment")
        rows.append({"feature": name, "arch": owner, "PASS": int(passed),
                     "FAIL": int(not passed), "N/A": 0, "reason": reason,
                     "checks": checks, "untested": untested})

    return rows


def rst_table(headers: list[str], rows: list[list[str]]) -> str:
    """Create an RST grid table for manually consumed text reports."""
    values = [headers] + rows
    widths = [max(len(str(row[index])) for row in values) for index in range(len(headers))]
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    heading = "+" + "+".join("=" * (width + 2) for width in widths) + "+"
    lines = [border]
    for index, row in enumerate(values):
        lines.append("| " + " | ".join(str(cell).ljust(width) for cell, width in zip(row, widths)) + " |")
        lines.append(heading if index == 0 else border)
    return "\n".join(lines) + "\n"


def html_table(rows: list[dict]) -> str:
    """Render feature coverage directly to HTML, with diagnostic reasons."""
    body = []
    for row in rows:
        if not row.get("checks"):
            continue
        name = html.escape(row["feature"])
        reason = html.escape(row["reason"], quote=True)
        body.append(f'<tr title="{reason}"><td>{name}</td>'
                    f'<td class="PASS">{row["PASS"]}</td><td class="FAIL">{row["FAIL"]}</td>'
                    f'<td class="NA">{row.get("N/A", 0)}</td><td>{reason}</td></tr>')
    return ('<h2>Feature coverage</h2><table><thead><tr><th>Feature</th><th>PASS</th>'
            '<th>FAIL</th><th>N/A</th><th>Reason</th></tr></thead><tbody>' +
            "".join(body) + '</tbody></table>' + untested_html(rows))


def untested_rows(rows: list[dict]) -> list[list[str]]:
    """Keep recorded implementation gaps visible, including partial features."""
    return [[row["feature"], row["arch"], item["check"], item["reason"]]
            for row in rows for item in row.get("untested", [])]


def untested_html(rows: list[dict]) -> str:
    """Render a separate coverage backlog, which never feeds badge counts."""
    body = ["<tr>" + "".join("<td>" + html.escape(cell) + "</td>" for cell in row) + "</tr>"
            for row in untested_rows(rows)]
    if not body:
        return "<h2>Features not tested</h2><p>No implementation gaps recorded.</p>"
    return ('<h2>Features not tested</h2><p>These checks lack a test implementation or '
            'required platform support. They are N/A and excluded from regression badges.</p>'
            '<table><thead><tr><th>Feature</th><th>Architecture</th><th>Missing check</th>'
            '<th>Reason</th></tr></thead><tbody>' + "".join(body) + '</tbody></table>')


def write_feature_table(directory: str, rows: list[dict]) -> str:
    """Retain a simple feature table and machine-readable diagnostic details."""
    values = [[row['feature'], str(row['PASS']), str(row['FAIL']), str(row.get('N/A', 0)),
               row['reason']] for row in rows if row.get('checks')]
    table = rst_table(["Feature", "PASS", "FAIL", "N/A", "Reason"], values)
    table += "\nFeatures not tested\n-------------------\n\n"
    table += rst_table(["Feature", "Architecture", "Missing check", "Reason"], untested_rows(rows))
    with open(os.path.join(directory, "features.rst"), "w", encoding="utf-8") as stream:
        stream.write(table)
    with open(os.path.join(directory, "features.json"), "w", encoding="utf-8") as stream:
        json.dump(rows, stream, indent=2)
        stream.write("\n")

    return table

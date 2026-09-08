#!/usr/bin/env python3
#
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Mauro Carvalho Chehab <mchehab+huawei@kernel.org>

"""Build one static Pages report from QEMU result artifacts."""

import argparse
import html
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import features  # pylint: disable=C0413


RESULT_HEADING = "<h1>Kernel and rasdaemon results</h1>"
SAFE_LABEL = re.compile(r"^[a-z0-9_-]+$")
SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")


class PublishedResult:  # pylint: disable=R0903
    """One architecture or fuzz-mode result within a published report."""

    def __init__(self, label: str, directory: str) -> None:
        if not SAFE_LABEL.fullmatch(label):
            raise ValueError(f"invalid result label: {label}")

        self.label = label
        self.directory = directory
        self.data = self._read_json("result.json")
        self.revision = self._read_text("rasdaemon-source-revision")

        if not SOURCE_REVISION.fullmatch(self.revision):
            raise ValueError("invalid rasdaemon source revision")

    def _read_json(self, filename: str) -> dict:
        path = os.path.join(self.directory, filename)

        with open(path, encoding="utf-8") as stream:
            return json.load(stream)

    def _read_text(self, filename: str) -> str:
        path = os.path.join(self.directory, filename)

        with open(path, encoding="utf-8") as stream:
            return stream.read().strip()


class ResultSite:  # pylint: disable=R0903
    """Publish a daily or weekly report and a compact README badge."""

    def __init__(self, title: str, site_dir: str, run_url: str,  # pylint: disable=R0913
                 source_repository: str, conclusion: str,
                 results: list[PublishedResult]) -> None:
        self.title = title
        self.site_dir = site_dir
        self.run_url = run_url
        self.source_repository = source_repository
        self.conclusion = conclusion
        self.results = results

    @staticmethod
    def _infrastructure_failure(result: PublishedResult) -> str:
        reason = result.data.get("infrastructure_failure", "")
        if reason:
            return reason
        # Compatibility with reports created before the explicit field existed.
        for test in result.data.get("tests", []):
            if (test.get("name") == "guest" and test.get("status") == "failed" and
                    test.get("rasdaemon") == "SKIP"):
                return test.get("reason", "guest failed before tests ran")
        return ""

    @staticmethod
    def _vm_status(result: PublishedResult) -> str:
        """Return completed, partial, failed, or unavailable VM health."""
        status = result.data.get("vm_status")
        if status in ("completed", "partial", "failed"):
            if status == "completed" and ResultSite._infrastructure_failure(result):
                return "failed"
            return status
        if ResultSite._infrastructure_failure(result):
            return "failed"
        # Reports written before vm_status was introduced completed the
        # document only after the guest returned; retain that interpretation.
        return "completed"

    def _totals(self) -> dict:
        totals = {"passed": 0, "failed": 0,
                  "infrastructure": {"x86_64": 0, "aarch64": 0},
                  "vm_status": {"x86_64": "unavailable", "aarch64": "unavailable"},
                  "components": {component: {state: 0 for state in
                                ("passed", "failed", "skipped", "not_applicable")}
                               for component in ("kernel", "rasdaemon")}}
        for result in self.results:
            arch = result.data.get("architecture", result.label)
            if arch in totals["vm_status"]:
                totals["vm_status"][arch] = self._vm_status(result)
            for component, values in result.data.get("component_totals", {}).items():
                if component in totals["components"]:
                    for state in totals["components"][component]:
                        totals["components"][component][state] += int(values.get(state, 0))
            if self._infrastructure_failure(result):
                if arch in totals["infrastructure"]:
                    totals["infrastructure"][arch] = 1
            # Feature rows already mark unexecuted checks N/A. Retain actual
            # passes and failures even if the VM stopped later in the suite.
            for row in result.data.get("features", []):
                if row.get("checks"):
                    totals["passed"] += int(bool(row.get("PASS")))
                    totals["failed"] += int(bool(row.get("FAIL")))
        return totals

    @staticmethod
    def _write_badge(path: str, label: str, value: int | str, color: str) -> None:
        title = html.escape(f"{label}: {value}", quote=True)
        safe_value = html.escape(str(value))
        badge = f'''<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"
 role="img" aria-label="{title}"><title>{title}</title>
<rect width="40" height="20" rx="3" fill="{color}"/>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif"
 font-size="11"><text x="20" y="14">{safe_value}</text></g></svg>
'''

        with open(path, "w", encoding="utf-8") as stream:
            stream.write(badge)

    @staticmethod
    def _write_vm_badge(path: str, label: str, status: str) -> None:
        """Write a compact, high-signal VM health indicator."""
        symbols = {"completed": "⬤", "partial": "⬤", "failed": "⛔",
                   "unavailable": "?"}
        colors = {"completed": "#2da44e", "partial": "#bf8700",
                  "failed": "#d73a49", "unavailable": "#6e7781"}
        symbol = symbols.get(status, "?")
        color = colors.get(status, colors["unavailable"])
        title = html.escape(f"{label}: {status}", quote=True)
        badge = f'''<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"
 role="img" aria-label="{title}"><title>{title}</title>
<text x="16" y="24" fill="{color}" text-anchor="middle"
 font-family="DejaVu Sans,sans-serif" font-size="25">{symbol}</text></svg>
'''
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(badge)

    def _write_badges(self, totals: dict | None) -> None:
        """Write feature-regression and per-architecture VM badges."""
        values = {
            "feature-pass": ("features PASS", totals["passed"] if totals else "N/A"),
            "feature-fail": ("features FAIL", totals["failed"] if totals else "N/A"),
        }
        if totals:
            for component in ("kernel", "rasdaemon"):
                label = component + " "
                values.update({
                    f"{component}-pass": (label + "PASS",
                                          totals["components"][component]["passed"]),
                    f"{component}-fail": (label + "FAIL",
                                          totals["components"][component]["failed"]),
                    f"{component}-skip": (label + "SKIP",
                                          totals["components"][component]["skipped"]),
                })
        else:
            for component in ("kernel", "rasdaemon"):
                for state in ("pass", "fail", "skip"):
                    values[f"{component}-{state}"] = (component + " " + state.upper(), "N/A")
        for filename, (label, value) in values.items():
            color = "#6e7781" if totals is None else (
                "#d73a49" if filename.endswith("fail") and value else "#2da44e")
            self._write_badge(os.path.join(self.site_dir, f"badge-{filename}.svg"),
                              label, value, color)
        for filename, label, arch in (("x86-vm-fail", "x86 VM", "x86_64"),
                                      ("arm64-vm-fail", "ARM64 VM", "aarch64")):
            status = totals["vm_status"][arch] if totals else "unavailable"
            self._write_vm_badge(os.path.join(self.site_dir, f"badge-{filename}.svg"),
                                 label, status)

    def _metadata_html(self, result: PublishedResult) -> str:
        run_url = html.escape(self.run_url, quote=True)
        repository = self.source_repository.rstrip("/")
        source_url = html.escape(f"{repository}/commit/{result.revision}",
                                 quote=True)
        finished = html.escape(result.data.get("finished_at", "unknown"))

        return f'''<p>Completed {finished}. Tested rasdaemon revision
<a href="{source_url}"><code>{result.revision[:12]}</code></a>.
See the <a href="{run_url}">GitHub Actions run</a> for the workflow log and artifact.</p>
<p>Download: <a href="summary.rst">summary</a>, <a href="result.json">JSON</a>,
<a href="junit.xml">JUnit</a>, <a href="results.log">result log</a>,
<a href="console.log">guest console</a>, <a href="qemu.log">QEMU log</a>.</p>'''

    def _publish_detail(self, result: PublishedResult, directory: str) -> None:
        shutil.copytree(result.directory, directory)
        report_path = os.path.join(result.directory, "results.html")

        with open(report_path, encoding="utf-8") as stream:
            report = stream.read()

        if RESULT_HEADING not in report:
            raise ValueError("results.html does not contain the expected heading")

        heading = RESULT_HEADING + self._metadata_html(result)
        page = report.replace(RESULT_HEADING, heading, 1)

        with open(os.path.join(directory, "index.html"), "w", encoding="utf-8") as stream:
            stream.write(page)

    def _write_index(self, totals: dict) -> None:
        run_url = html.escape(self.run_url, quote=True)
        assets = html.escape(self.results[0].label, quote=True)
        reports = ""
        feature_rows = []
        other_rows = []
        functional_checks = {check for checks in features.FEATURES.values()
                             for check in checks[1]}

        for result in self.results:
            link = html.escape(result.label, quote=True)
            label = html.escape(result.label)
            reports += f'<li><a href="{link}/">{label} report</a></li>'
            feature_rows.extend(result.data.get("features", []))
            for test in result.data.get("tests", []):
                if test.get("name") in functional_checks or test.get("name") in {
                        "feature-coverage", "coverage-contract"}:
                    continue
                other_rows.append((result.label, test))
        feature_table = features.html_table(sorted(feature_rows, key=lambda row: row["feature"]))
        other_body = "".join(
            f'<tr><td>{html.escape(label)}</td><td>{html.escape(test["name"])}</td>'
            f'<td class="{html.escape(test["status"].upper())}">{html.escape(test["status"].upper())}</td>'
            f'<td>{html.escape(test.get("reason", ""))}</td></tr>'
            for label, test in other_rows)
        other_table = ('<h2>Other checks</h2><p>Internal build, setup, lifecycle and '
                       'consumer checks are shown here; they are not feature badge totals.</p>'
                       '<table><thead><tr><th>Report</th><th>Check</th><th>Status</th>'
                       '<th>Reason</th></tr></thead><tbody>' + other_body +
                       '</tbody></table>') if other_rows else ""
        page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(self.title)}</title><link rel="stylesheet" href="{assets}/results.css">
<script src="{assets}/results.js" defer></script></head><body>
<button type="button" id="theme" class="theme-toggle" title="Change color theme"
 aria-label="Change color theme">◐ Auto</button>
<h1>{html.escape(self.title)}</h1><p>Latest published result. See the
<a href="{run_url}">GitHub Actions run</a> for the complete workflow log.</p>
{feature_table}
{other_table}
<h2>Run status</h2><table><thead><tr><th>Features PASS</th><th>Features FAIL</th>
<th>x86 VM</th><th>ARM64 VM</th></tr></thead><tbody><tr>
<td>{totals['passed']}</td><td>{totals['failed']}</td>
<td>{totals['vm_status']['x86_64']}</td>
<td>{totals['vm_status']['aarch64']}</td></tr></tbody></table>
<p>VM status is completed, partial, failed, or unavailable. Unexecuted features
are not counted as regressions.</p>
<h2>Reports</h2><ul>{reports}</ul>
</body></html>'''

        with open(os.path.join(self.site_dir, "index.html"), "w", encoding="utf-8") as stream:
            stream.write(page)

    def write(self) -> None:
        """Copy evidence, add provenance, aggregate totals and create a badge."""
        totals = self._totals()

        if os.path.exists(self.site_dir):
            shutil.rmtree(self.site_dir)

        os.makedirs(self.site_dir)

        if not self.results:
            page = ("<!doctype html><meta charset=\"utf-8\"><title>" +
                    html.escape(self.title) + "</title><h1>" +
                    html.escape(self.title) +
                    "</h1><p>No compatible result has been published yet.</p>")

            with open(os.path.join(self.site_dir, "index.html"), "w",
                      encoding="utf-8") as stream:
                stream.write(page)

            self._write_badges(None)

            return

        if len(self.results) == 1:
            result = self.results[0]
            shutil.rmtree(self.site_dir)
            self._publish_detail(result, self.site_dir)
        else:
            for result in self.results:
                directory = os.path.join(self.site_dir, result.label)
                self._publish_detail(result, directory)

            self._write_index(totals)

        self._write_badges(totals)


def result_argument(value: str) -> PublishedResult:
    """Parse LABEL=DIRECTORY into one result."""
    label, separator, directory = value.partition("=")
    if not separator or not directory:
        raise argparse.ArgumentTypeError("result must use LABEL=DIRECTORY")

    try:
        return PublishedResult(label, directory)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def parse_args() -> argparse.Namespace:
    """Parse site publishing arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--conclusion", required=True)
    parser.add_argument("--result", action="append", default=[],
                        type=result_argument, metavar="LABEL=DIRECTORY")

    return parser.parse_args()


def main() -> int:
    """Build the selected latest-results site."""
    args = parse_args()
    site = ResultSite(args.title, args.site, args.run_url,
                      args.source_repository, args.conclusion, args.result)
    site.write()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

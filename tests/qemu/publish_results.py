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

    def _totals(self) -> dict:
        totals = {"passed": 0, "failed": 0,
                  "infrastructure": {"x86_64": 0, "aarch64": 0}}
        for result in self.results:
            if self._infrastructure_failure(result):
                arch = result.data.get("architecture", result.label)
                if arch in totals["infrastructure"]:
                    totals["infrastructure"][arch] = 1
                continue
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

    def _write_badges(self, totals: dict | None) -> None:
        """Write feature-regression and per-architecture VM badges."""
        values = {
            "feature-pass": ("features PASS", totals["passed"] if totals else "N/A"),
            "feature-fail": ("features FAIL", totals["failed"] if totals else "N/A"),
            "x86-vm-fail": ("x86 VM FAIL", totals["infrastructure"]["x86_64"]
                            if totals else "N/A"),
            "arm64-vm-fail": ("ARM64 VM FAIL", totals["infrastructure"]["aarch64"]
                              if totals else "N/A"),
        }
        for filename, (label, value) in values.items():
            color = "#6e7781" if totals is None else (
                "#d73a49" if filename.endswith("fail") and value else "#2da44e")
            self._write_badge(os.path.join(self.site_dir, f"badge-{filename}.svg"),
                              label, value, color)

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

        for result in self.results:
            link = html.escape(result.label, quote=True)
            label = html.escape(result.label)
            reports += f'<li><a href="{link}/">{label} report</a></li>'
            feature_rows.extend(result.data.get("features", []))
        feature_table = features.html_table(sorted(feature_rows, key=lambda row: row["feature"]))
        page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(self.title)}</title><link rel="stylesheet" href="{assets}/results.css">
<script src="{assets}/results.js" defer></script></head><body>
<button type="button" id="theme" class="theme-toggle" title="Change color theme"
 aria-label="Change color theme">◐ Auto</button>
<h1>{html.escape(self.title)}</h1><p>Latest published result. See the
<a href="{run_url}">GitHub Actions run</a> for the complete workflow log.</p>
{feature_table}
<h2>Run status</h2><table><thead><tr><th>Features PASS</th><th>Features FAIL</th>
<th>x86 VM FAIL</th><th>ARM64 VM FAIL</th></tr></thead><tbody><tr>
<td>{totals['passed']}</td><td>{totals['failed']}</td>
<td>{totals['infrastructure']['x86_64']}</td>
<td>{totals['infrastructure']['aarch64']}</td></tr></tbody></table>
<p>A VM failure means that architecture could not execute its functional tests.
Unexecuted features are not counted as regressions.</p>
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

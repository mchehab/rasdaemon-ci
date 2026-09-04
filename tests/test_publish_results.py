# SPDX-License-Identifier: GPL-2.0-only
"""Verify publication of durable daily and weekly reports."""

import json
import os
import tempfile
import unittest

from tests.qemu.publish_results import PublishedResult, ResultSite


class ResultSiteTest(unittest.TestCase):
    """Published sites expose provenance and independent component totals."""

    @staticmethod
    def _write_result(directory: str, revision: str, failed: int = 0) -> None:
        os.makedirs(directory)
        data = {
            "finished_at": "2026-09-06T12:00:00+00:00",
            "component_totals": {
                "kernel": {"passed": 20, "failed": 0, "skipped": 2,
                           "not_applicable": 0},
                "rasdaemon": {"passed": 18, "failed": failed, "skipped": 3,
                              "not_applicable": 2},
            },
        }

        with open(os.path.join(directory, "result.json"), "w", encoding="utf-8") as stream:
            json.dump(data, stream)

        with open(os.path.join(directory, "results.html"), "w", encoding="utf-8") as stream:
            stream.write("<html><h1>Kernel and rasdaemon results</h1></html>")

        with open(os.path.join(directory, "rasdaemon-source-revision"), "w",
                  encoding="utf-8") as stream:
            stream.write(revision + "\n")

        for filename in ("summary.md", "junit.xml", "results.log", "console.log", "qemu.log"):
            with open(os.path.join(directory, filename), "w", encoding="utf-8") as stream:
                stream.write(filename)

    def test_single_result_is_the_report_landing_page(self) -> None:
        """The daily badge opens the detailed report without another click."""
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = os.path.join(temporary, "daily")
            site_dir = os.path.join(temporary, "site")
            self._write_result(result_dir, "a" * 40)
            result = PublishedResult("x86_64", result_dir)
            site = ResultSite(
                "Daily media CI tests", site_dir,
                "https://github.com/mchehab/rasdaemon-ci/actions/runs/42",
                "https://github.com/mchehab/rasdaemon", "success", [result],
            )
            site.write()

            with open(os.path.join(site_dir, "index.html"), encoding="utf-8") as stream:
                page = stream.read()

            badges = {}

            for outcome in ("pass", "fail", "skip", "n-a"):
                path = os.path.join(site_dir, f"badge-kernel-{outcome}.svg")
                with open(path, encoding="utf-8") as stream:
                    badges[outcome] = stream.read()

        self.assertIn("actions/runs/42", page)
        self.assertIn("rasdaemon/commit/" + "a" * 40, page)
        self.assertIn("kernel PASS: 20", badges["pass"])
        self.assertIn("#2da44e", badges["pass"])
        self.assertIn("#2da44e", badges["fail"])
        self.assertIn("#bf8700", badges["skip"])
        self.assertIn("#2da44e", badges["n-a"])
        self.assertIn('width="40" height="20"', badges["pass"])
        self.assertIn('<text x="20" y="14">20</text>', badges["pass"])

    def test_multiple_results_are_aggregated_and_linked(self) -> None:
        """The weekly page totals all fuzz modes and retains each full report."""
        with tempfile.TemporaryDirectory() as temporary:
            random_dir = os.path.join(temporary, "random")
            zero_dir = os.path.join(temporary, "zero")
            site_dir = os.path.join(temporary, "site")
            self._write_result(random_dir, "b" * 40)
            self._write_result(zero_dir, "b" * 40, failed=1)
            results = [PublishedResult("random", random_dir),
                       PublishedResult("zero", zero_dir)]
            site = ResultSite("Weekly media CI tests", site_dir,
                              "https://example.test/run",
                              "https://example.test/source", "failure", results)
            site.write()

            with open(os.path.join(site_dir, "index.html"), encoding="utf-8") as stream:
                page = stream.read()

            with open(os.path.join(site_dir, "badge-rasdaemon-fail.svg"),
                      encoding="utf-8") as stream:
                badge = stream.read()

            with open(os.path.join(site_dir, "badge-rasdaemon-n-a.svg"),
                      encoding="utf-8") as stream:
                not_applicable_badge = stream.read()

        self.assertIn("random report", page)
        self.assertIn("zero report", page)
        self.assertIn('class="theme-toggle"', page)
        self.assertIn('random/results.css', page)
        self.assertIn("<th>rasdaemon</th><td>36</td><td>1</td><td>6</td>", page)
        self.assertIn("rasdaemon FAIL: 1", badge)
        self.assertIn("#d73a49", badge)
        self.assertIn("rasdaemon N/A: 4", not_applicable_badge)
        self.assertIn("#6e7781", not_applicable_badge)

    def test_invalid_source_revision_is_rejected(self) -> None:
        """Never place arbitrary artifact text into a source link."""
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = os.path.join(temporary, "result")
            self._write_result(result_dir, "not-a-git-revision")

            with self.assertRaisesRegex(ValueError, "invalid rasdaemon source revision"):
                PublishedResult("random", result_dir)

    def test_missing_first_result_gets_a_neutral_badge(self) -> None:
        """Pages can start before both new report cadences have completed."""
        with tempfile.TemporaryDirectory() as temporary:
            site_dir = os.path.join(temporary, "site")
            site = ResultSite("Weekly media CI tests", site_dir,
                              "https://example.test/run",
                              "https://example.test/source", "unavailable", [])
            site.write()

            badges = []

            for component in ("kernel", "rasdaemon"):
                for outcome in ("pass", "fail", "skip", "n-a"):
                    path = os.path.join(
                        site_dir, f"badge-{component}-{outcome}.svg"
                    )
                    with open(path, encoding="utf-8") as stream:
                        badges.append(stream.read())

        self.assertEqual(len(badges), 8)

        for badge in badges:
            self.assertIn(">N/A</text>", badge)
            self.assertIn("#6e7781", badge)


if __name__ == "__main__":
    unittest.main()

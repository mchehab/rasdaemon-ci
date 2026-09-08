# SPDX-License-Identifier: GPL-2.0-only
"""Verify publication of durable daily and weekly reports."""

import json
import os
import tempfile
import unittest

from tests.qemu.publish_results import PublishedResult, ResultSite


class ResultSiteTest(unittest.TestCase):
    """Published sites expose provenance, feature totals and VM health."""

    @staticmethod
    def _write_result(directory: str, revision: str, failed: int = 0,
                      arch: str = "x86_64", infrastructure: str = "",
                      vm_status: str = "completed") -> None:
        os.makedirs(directory)
        data = {
            "finished_at": "2026-09-06T12:00:00+00:00",
            "architecture": arch,
            "vm_status": vm_status,
            "features": [
                {"feature": "aer", "arch": arch, "PASS": int(not failed),
                 "FAIL": int(bool(failed)), "N/A": 0, "reason": "result",
                 "checks": ["aer-native"], "untested": []},
            ],
            "component_totals": {
                "kernel": {"passed": 20, "failed": 0, "skipped": 2,
                           "not_applicable": 0},
                "rasdaemon": {"passed": 18, "failed": failed, "skipped": 3,
                              "not_applicable": 2},
            },
        }
        if infrastructure:
            data["infrastructure_failure"] = infrastructure

        with open(os.path.join(directory, "result.json"), "w", encoding="utf-8") as stream:
            json.dump(data, stream)

        with open(os.path.join(directory, "results.html"), "w", encoding="utf-8") as stream:
            stream.write("<html><h1>Kernel and rasdaemon results</h1></html>")

        with open(os.path.join(directory, "rasdaemon-source-revision"), "w",
                  encoding="utf-8") as stream:
            stream.write(revision + "\n")

        for filename in ("summary.rst", "junit.xml", "results.log", "console.log", "qemu.log"):
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
            for outcome in ("feature-pass", "feature-fail", "kernel-pass",
                            "kernel-fail", "kernel-skip", "rasdaemon-pass",
                            "rasdaemon-fail", "rasdaemon-skip", "x86-vm-fail",
                            "arm64-vm-fail"):
                path = os.path.join(site_dir, f"badge-{outcome}.svg")
                with open(path, encoding="utf-8") as stream:
                    badges[outcome] = stream.read()

        self.assertIn("actions/runs/42", page)
        self.assertIn("rasdaemon/commit/" + "a" * 40, page)
        self.assertIn("features PASS: 1", badges["feature-pass"])
        self.assertIn("#2da44e", badges["feature-pass"])
        self.assertIn("features FAIL: 0", badges["feature-fail"])
        self.assertIn("x86 VM: completed", badges["x86-vm-fail"])
        self.assertIn("ARM64 VM: unavailable", badges["arm64-vm-fail"])
        self.assertIn(">⬤</text>", badges["x86-vm-fail"])
        self.assertIn('width="40" height="20"', badges["feature-pass"])

    def test_multiple_results_are_aggregated_and_linked(self) -> None:
        """The weekly page totals all fuzz modes and retains each full report."""
        with tempfile.TemporaryDirectory() as temporary:
            random_dir = os.path.join(temporary, "random")
            zero_dir = os.path.join(temporary, "zero")
            site_dir = os.path.join(temporary, "site")
            self._write_result(random_dir, "b" * 40)
            self._write_result(zero_dir, "b" * 40, failed=1, arch="aarch64")
            results = [PublishedResult("random", random_dir),
                       PublishedResult("zero", zero_dir)]
            site = ResultSite("Weekly media CI tests", site_dir,
                              "https://example.test/run",
                              "https://example.test/source", "failure", results)
            site.write()

            with open(os.path.join(site_dir, "index.html"), encoding="utf-8") as stream:
                page = stream.read()

            with open(os.path.join(site_dir, "badge-feature-fail.svg"),
                      encoding="utf-8") as stream:
                badge = stream.read()

        self.assertIn("random report", page)
        self.assertIn("zero report", page)
        self.assertIn('class="theme-toggle"', page)
        self.assertIn('random/results.css', page)
        self.assertIn("<td>1</td><td>1</td>", page)
        self.assertIn("features FAIL: 1", badge)
        self.assertIn("#d73a49", badge)

    def test_infrastructure_failure_keeps_completed_feature_verdicts(self) -> None:
        """A later VM failure must not discard already measured regressions."""
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = os.path.join(temporary, "arm")
            site_dir = os.path.join(temporary, "site")
            self._write_result(result_dir, "c" * 40, failed=1,
                               arch="aarch64", infrastructure="QEMU exited")
            site = ResultSite("Daily", site_dir, "https://example.test/run",
                              "https://example.test/source", "failure",
                              [PublishedResult("aarch64", result_dir)])
            site.write()
            with open(os.path.join(site_dir, "badge-feature-fail.svg"),
                      encoding="utf-8") as stream:
                feature_badge = stream.read()
            with open(os.path.join(site_dir, "badge-arm64-vm-fail.svg"),
                      encoding="utf-8") as stream:
                vm_badge = stream.read()
        self.assertIn("features FAIL: 1", feature_badge)
        self.assertIn("ARM64 VM: failed", vm_badge)
        self.assertIn(">⛔</text>", vm_badge)

    def test_partial_vm_has_yellow_open_circle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = os.path.join(temporary, "arm")
            site_dir = os.path.join(temporary, "site")
            self._write_result(result_dir, "d" * 40, arch="aarch64",
                               vm_status="partial", infrastructure="timeout")
            ResultSite("Daily", site_dir, "https://example.test/run",
                       "https://example.test/source", "failure",
                       [PublishedResult("aarch64", result_dir)]).write()
            with open(os.path.join(site_dir, "badge-arm64-vm-fail.svg"),
                      encoding="utf-8") as stream:
                badge = stream.read()
        self.assertIn("ARM64 VM: partial", badge)
        self.assertIn(">⬤</text>", badge)
        self.assertIn("#bf8700", badge)

    def test_na_does_not_inflate_totals_after_vm_failure(self) -> None:
        """Count a completed pass while leaving an unexecuted feature neutral."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = os.path.join(temporary, "arm")
            self._write_result(directory, "e" * 40, arch="aarch64",
                               vm_status="partial", infrastructure="timeout")
            result = PublishedResult("aarch64", directory)
            neutral = dict(result.data["features"][0])
            neutral.update({"feature": "arm", "PASS": 0, "FAIL": 0, "N/A": 1})
            result.data["features"].append(neutral)
            site = ResultSite("Daily", os.path.join(temporary, "site"),
                              "https://example.test/run", "https://example.test/source",
                              "failure", [result])
            totals = site._totals()  # pylint: disable=W0212

        self.assertEqual((totals["passed"], totals["failed"]), (1, 0))
        self.assertEqual(totals["vm_status"]["aarch64"], "partial")

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

            for name in ("feature-pass", "feature-fail", "kernel-pass",
                         "kernel-fail", "kernel-skip", "rasdaemon-pass",
                         "rasdaemon-fail", "rasdaemon-skip", "x86-vm-fail",
                         "arm64-vm-fail"):
                with open(os.path.join(site_dir, f"badge-{name}.svg"),
                          encoding="utf-8") as stream:
                    badges.append(stream.read())

        self.assertEqual(len(badges), 10)

        for badge in badges[:8]:
            self.assertIn(">N/A</text>", badge)
            self.assertIn("#6e7781", badge)
        for badge in badges[8:]:
            self.assertIn("unavailable", badge)
            self.assertIn("#6e7781", badge)


if __name__ == "__main__":
    unittest.main()

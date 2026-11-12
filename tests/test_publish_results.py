# SPDX-License-Identifier: GPL-2.0-only
"""Verify publication of durable daily and weekly reports."""

import json
import os
import tempfile
import unittest
import datetime

from tests.qemu.publish_results import PublishedResult, ResultSite, format_version
from tests.qemu.archive_results import archive


class ResultSiteTest(unittest.TestCase):
    """Published sites expose provenance, feature totals and VM health."""

    def test_version_uses_tag_only_for_matching_commit_and_version(self) -> None:
        commit = "a" * 40
        tags = {"commit": commit, "tags": ["v7.4-rc1", "v1.0.0"]}
        self.assertEqual(format_version("7.4.0-rc1", commit, tags), "v7.4-rc1")
        self.assertEqual(format_version("1.0.0", commit, tags), "v1.0.0")
        self.assertEqual(format_version("7.4.0-rc2", commit, tags), "7.4.0-rc2-aaaaaaaaaaaa")
        self.assertEqual(format_version("7.4.0-rc1", "b" * 40, tags), "7.4.0-rc1-bbbbbbbbbbbb")
        self.assertEqual(format_version("", commit, tags), "N/A")

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

    def test_weekly_vm_health_keeps_worst_mode_in_any_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = []
            for mode, status in (("random", "failed"), ("zero", "partial"),
                                 ("boundaries", "completed")):
                directory = os.path.join(temporary, mode)
                self._write_result(directory, "a" * 40, vm_status=status)
                results.append(PublishedResult(mode, directory))
            for selected, expected in ((results, "failed"), (results[::-1], "failed"),
                                       (results[1:], "partial"), (results[:1], "failed")):
                site = ResultSite("Weekly", os.path.join(temporary, "site"),
                                  "https://example.test/run", "https://example.test/source",
                                  "failure", selected)
                site.write()
                with open(os.path.join(site.site_dir, "index.html"), encoding="utf-8") as stream:
                    self.assertIn("Fuzz test summary", stream.read())
                for result in selected:
                    detail = os.path.join(site.site_dir, result.label)
                    self.assertTrue(os.path.isfile(os.path.join(detail, "results.css")))
                    with open(os.path.join(detail, "index.html"), encoding="utf-8") as stream:
                        self.assertIn("../badge-qemu-", stream.read())
                with open(os.path.join(site.site_dir, "badge-x86-vm-fail.svg"),
                          encoding="utf-8") as stream:
                    self.assertIn("x86 VM: " + expected, stream.read())

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
        self.assertIn('<circle cx="16" cy="16" r="12" fill="#2da44e"/>',
                      badges["x86-vm-fail"])
        self.assertIn('width="40" height="20"', badges["feature-pass"])

    def test_versions_link_to_the_tested_sources(self) -> None:
        """Version badges identify the image and the exact source commits."""
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = os.path.join(temporary, "result")
            site_dir = os.path.join(temporary, "site")
            self._write_result(result_dir, "a" * 40, arch="aarch64")
            with open(os.path.join(result_dir, "result.json"), encoding="utf-8") as stream:
                data = json.load(stream)
            data["capabilities"] = [{"name": "qemu", "value": "QEMU emulator version 11.1.1"}]
            with open(os.path.join(result_dir, "result.json"), "w", encoding="utf-8") as stream:
                json.dump(data, stream)
            for name, metadata in (("kernel", {"release": "7.3.0-gabc", "repository": "https://github.com/torvalds/linux.git", "commit": "b" * 40}),
                                   ("qemu", {"repository": "https://gitlab.com/example/qemu.git", "commit": "c" * 40})):
                with open(os.path.join(result_dir, name + ".json"), "w", encoding="utf-8") as stream:
                    json.dump(metadata, stream)
            with open(os.path.join(result_dir, "kernel-tags.json"), "w", encoding="utf-8") as stream:
                json.dump({"commit": "b" * 40, "tags": ["v7.3"]}, stream)
            with open(os.path.join(result_dir, "qemu-describe.json"), "w", encoding="utf-8") as stream:
                json.dump({"commit": "c" * 40, "describe": "v11.1.0-5-g1234567"}, stream)
            with open(os.path.join(result_dir, "console.log"), "w", encoding="utf-8") as stream:
                stream.write("rasdaemon payload version=rasdaemon 1.0.0; source HEAD=unknown\n")
            ResultSite("Daily", site_dir, "https://example.test/run", "https://github.com/mchehab/rasdaemon", "failure", [PublishedResult("aarch64", result_dir)]).write()
            for name, version, target in (("qemu", "v11.1.0-5-g1234567", "/-/commit/" + "c" * 40),
                                          ("kernel", "v7.3", "/commit/" + "b" * 40),
                                          ("rasdaemon", "1.0.0-aaaaaaaaaaaa", "/commit/" + "a" * 40)):
                with open(os.path.join(site_dir, f"badge-{name}-aarch64.svg"), encoding="utf-8") as stream:
                    self.assertIn(version, stream.read())
                with open(os.path.join(site_dir, f"{name}-aarch64.html"), encoding="utf-8") as stream:
                    self.assertIn(target, stream.read())

    def test_archive_preserves_version_evidence(self) -> None:
        """Archived reports retain the run's own version data and links."""
        with tempfile.TemporaryDirectory() as temporary:
            source = os.path.join(temporary, "input")
            result_dir = os.path.join(source, "aarch64")
            self._write_result(result_dir, "a" * 40, arch="aarch64")
            with open(os.path.join(source, "run"), "w", encoding="utf-8") as stream:
                stream.write("https://example.test/run\tfailure\t2026-11-06T04:00:00Z\t42\n")
            metadata = {"release": "7.3.0-gabc", "repository": "https://github.com/torvalds/linux.git", "commit": "b" * 40}
            with open(os.path.join(result_dir, "kernel.json"), "w", encoding="utf-8") as stream:
                json.dump(metadata, stream)
            store = os.path.join(temporary, "store")
            site = os.path.join(temporary, "site")
            archive(store, site, ["daily=" + source],
                    datetime.datetime(2026, 11, 6, tzinfo=datetime.timezone.utc))
            name = "20261106T040000Z-42"
            with open(os.path.join(store, name, "daily", "aarch64", "kernel.json"), encoding="utf-8") as stream:
                self.assertEqual(json.load(stream), metadata)
            report = os.path.join(site, name, "daily")
            with open(os.path.join(report, "index.html"), encoding="utf-8") as stream:
                self.assertIn('badge-kernel-aarch64.svg', stream.read())
            with open(os.path.join(report, "badge-kernel-aarch64.svg"), encoding="utf-8") as stream:
                self.assertIn("7.3.0", stream.read())
            with open(os.path.join(report, "kernel-aarch64.html"), encoding="utf-8") as stream:
                self.assertIn("/commit/" + "b" * 40, stream.read())
            with open(os.path.join(site, "archive", "index.html"), encoding="utf-8") as stream:
                page = stream.read()
            self.assertIn("7.3.0", page)
            self.assertIn(f"../{name}/daily/kernel-aarch64.html", page)
            with open(os.path.join(store, name, "daily.json"), encoding="utf-8") as stream:
                saved = json.load(stream)
            self.assertEqual(saved["versions"]["kernel"], "7.3.0-bbbbbbbbbbbb")

            # Reprocessing an old archive must not invent version metadata.
            saved.pop("versions")
            saved.pop("version_label")
            with open(os.path.join(store, name, "daily.json"), "w", encoding="utf-8") as stream:
                json.dump(saved, stream)
            archive(store, site, ["daily=" + source],
                    datetime.datetime(2026, 11, 6, tzinfo=datetime.timezone.utc))
            with open(os.path.join(store, name, "daily.json"), encoding="utf-8") as stream:
                self.assertNotIn("versions", json.load(stream))

    def test_multiple_results_are_aggregated_and_linked(self) -> None:
        """The weekly page totals all fuzz modes and retains each full report."""
        with tempfile.TemporaryDirectory() as temporary:
            random_dir = os.path.join(temporary, "random")
            zero_dir = os.path.join(temporary, "zero")
            site_dir = os.path.join(temporary, "site")
            self._write_result(random_dir, "b" * 40)
            self._write_result(zero_dir, "b" * 40, failed=1,
                               vm_status="partial")
            results = [PublishedResult("random", random_dir),
                       PublishedResult("zero", zero_dir)]
            site = ResultSite("Weekly fuzz tests", site_dir,
                              "https://example.test/run",
                              "https://example.test/source", "failure", results)
            site.write()

            with open(os.path.join(site_dir, "index.html"), encoding="utf-8") as stream:
                page = stream.read()

            self.assertLess(page.index("Tested versions"), page.index("Fuzz test summary"))
            self.assertIn("badge-qemu-random.svg", page)

            with open(os.path.join(site_dir, "badge-feature-fail.svg"),
                      encoding="utf-8") as stream:
                badge = stream.read()

        self.assertIn("random report", page)
        self.assertIn("zero report", page)
        self.assertIn('<td class="PASS">40</td>', page)
        self.assertIn('<td class="PASS">36</td>', page)
        self.assertIn('<td class="FAIL">1</td>', page)
        self.assertIn('<td class="SKIP">partial</td>', page)
        self.assertIn('<td class="NA">4</td>', page)
        self.assertNotIn("ARM64 VM", page)
        self.assertNotIn("Feature coverage", page)
        self.assertNotIn("Feature status summary", page)
        self.assertIn("boundaries", page)
        self.assertIn('random/results.css', page)
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
        self.assertIn('<path d="M10 10l12 12m0-12L10 22"', vm_badge)

    def test_partial_vm_has_yellow_circle(self) -> None:
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
        self.assertIn('<circle cx="16" cy="16" r="12" fill="#bf8700"/>', badge)
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
            site = ResultSite("Weekly fuzz tests", site_dir,
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

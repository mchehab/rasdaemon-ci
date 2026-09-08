# SPDX-License-Identifier: GPL-2.0-only
"""Feature verdicts must distinguish unsupported hardware from missing tests."""

import importlib.util
import os
import tempfile
import unittest


MODULE_PATH = os.path.join(os.path.dirname(__file__), "qemu", "features.py")
SPEC = importlib.util.spec_from_file_location("feature_inventory", MODULE_PATH)
features = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(features)


class FeatureResultTest(unittest.TestCase):
    """Keep N/A explicit and prevent unsupported entries hiding regressions."""

    def test_server_databases_are_x86_only_but_sqlite_stays_on_arm(self):
        """Move only the server backends, preserving ARM SQLite coverage."""
        for name in ("consumer-mysql", "consumer-postgresql"):
            self.assertEqual(features.scenario_arch(name), "x86_64")
        self.assertEqual(features.scenario_arch("consumer-sqlite3"), "aarch64")
        scenarios = [{"name": name} for name in ("consumer-mysql", "consumer-postgresql")]
        arm = features.planned_tests("injection", "aarch64", scenarios)
        x86 = features.planned_tests("injection", "x86_64", scenarios)
        for scenario in scenarios:
            self.assertNotIn(scenario["name"], arm)
            self.assertIn(scenario["name"], x86)

    def test_reri_is_not_a_failure(self):
        row = features.feature_results("aarch64", [], ["reri"])[0]
        self.assertEqual((row["PASS"], row["FAIL"], row["N/A"]), (0, 0, 1))
        self.assertIn("no RERI producer", row["reason"])

    def test_missing_supported_check_is_a_failure(self):
        row = features.feature_results("aarch64", [], ["arm"])[0]
        self.assertEqual((row["PASS"], row["FAIL"], row["N/A"]), (0, 1, 0))

    def test_infrastructure_skip_is_not_a_feature_regression(self):
        row = features.feature_results("aarch64", [], ["arm"], "QEMU did not boot")[0]
        self.assertEqual((row["PASS"], row["FAIL"], row["N/A"]), (0, 0, 1))
        self.assertIn("QEMU did not boot", row["reason"])

    def test_unknown_feature_is_an_implementation_gap(self):
        row = features.feature_results("aarch64", [], ["new-feature"])[0]
        self.assertEqual(row["FAIL"], 0)
        self.assertEqual(row["PASS"], 0)
        self.assertEqual(row["N/A"], 1)
        self.assertIn("No functional test implementation", row["untested"][0]["reason"])

    def test_partial_feature_does_not_fail_for_unimplemented_checks(self):
        names = features.FEATURES["cxl"][1]
        tests = [{"name": name, "status": "passed"} for name in names
                 if name not in features.UNIMPLEMENTED_CHECKS]
        row = features.feature_results("x86_64", tests, ["cxl"])[0]
        self.assertEqual(row["FAIL"], 0)
        self.assertEqual(len(row["untested"]), 1)
        tests[0]["status"] = "failed"
        self.assertEqual(features.feature_results("x86_64", tests, ["cxl"])[0]["FAIL"], 1)

    def test_failed_skipped_and_duplicate_checks_do_not_pass(self):
        for statuses in (("failed",), ("skipped",), ("passed", "passed")):
            tests = [{"name": "ghes-arm", "status": status} for status in statuses]
            row = features.feature_results("aarch64", tests, ["arm"])[0]
            self.assertEqual(row["FAIL"], 1)
            self.assertEqual(row["N/A"], 0)

    def test_explicit_na_is_neither_success_nor_failure(self):
        """N/A must not hide another check's failure or inflate passing totals."""
        tests = [{"name": "aer-native", "status": "not_applicable"},
                 {"name": "ghes-aer", "status": "passed"}]
        row = features.feature_results("x86_64", tests, ["aer"])[0]
        self.assertEqual((row["PASS"], row["FAIL"], row["N/A"]), (0, 0, 1))
        tests[1]["status"] = "failed"
        row = features.feature_results("x86_64", tests, ["aer"])[0]
        self.assertEqual((row["PASS"], row["FAIL"], row["N/A"]), (0, 1, 0))

    def test_partial_run_keeps_completed_verdicts(self):
        """A later timeout only neutralizes checks that did not run."""
        tests = [{"name": "ghes-arm", "status": "passed"},
                 {"name": "block-io-native", "status": "failed"}]
        rows = features.feature_results("aarch64", tests,
                                        ["arm", "diskerror", "mc-event"], "timeout")
        self.assertEqual([(row["PASS"], row["FAIL"], row["N/A"]) for row in rows],
                         [(1, 0, 0), (0, 1, 0), (0, 0, 1)])

    def test_pass_has_one_owner(self):
        tests = [{"name": "ghes-arm", "status": "passed"}]
        rows = features.feature_results("aarch64", tests, ["arm"])
        self.assertEqual(rows[0]["PASS"], 1)
        self.assertEqual(features.feature_results("x86_64", tests, ["arm"]), [])

    def test_all_hisi_families_are_required(self):
        names = features.FEATURES["hisi-ns-decode"][1]
        self.assertEqual(len(names), 4)
        tests = [{"name": name, "status": "passed"} for name in names]
        self.assertEqual(features.feature_results("aarch64", tests, ["hisi-ns-decode"])[0]["PASS"], 1)
        self.assertEqual(features.feature_results("aarch64", tests[:-1], ["hisi-ns-decode"])[0]["FAIL"], 1)

    def test_reports_include_na_and_reason(self):
        rows = features.feature_results("aarch64", [], ["reri"])
        page = features.html_table(rows)
        self.assertIn("<th>N/A</th>", page)
        self.assertIn("Features not tested", page)
        self.assertIn(rows[0]["reason"], page)
        with tempfile.TemporaryDirectory() as directory:
            table = features.write_feature_table(directory, rows)
            self.assertIn("N/A", table)
            self.assertIn(rows[0]["reason"], table)

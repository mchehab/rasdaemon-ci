# SPDX-License-Identifier: GPL-2.0-only
"""Keep regressions permanently and expire infrastructure-only evidence after 30 days."""

import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "qemu"))
import archive_results  # pylint: disable=C0413
import retention  # pylint: disable=C0413


class RetentionTest(unittest.TestCase):
    """Infrastructure problems must not hide or expire real failed tests."""

    def test_setup_failure_is_temporary(self) -> None:
        """A QEMU launch error is not a test regression."""
        result = {"infrastructure_failure": "QEMU cannot start", "tests": [
            {"name": "guest", "status": "failed", "kernel": "SKIP", "rasdaemon": "SKIP"},
            {"name": "coverage-contract", "status": "failed"},
            {"name": "feature-coverage", "status": "failed"},
        ]}
        self.assertFalse(retention.test_failed(result))

    def test_mixed_failure_is_permanent(self) -> None:
        """A later VM abort must not erase an earlier failed assertion."""
        result = {"infrastructure_failure": "VM stopped", "tests": [
            {"name": "guest", "status": "failed", "kernel": "SKIP", "rasdaemon": "SKIP"},
            {"name": "aer-native", "status": "failed", "kernel": "PASS", "rasdaemon": "FAIL"},
        ]}
        self.assertTrue(retention.test_failed(result))

    def test_kernel_watchdog_failure_is_permanent(self) -> None:
        """An observed kernel crash is evidence even when it aborts the VM."""
        result = {"tests": [{"name": "guest", "status": "failed",
                              "kernel": "FAIL", "rasdaemon": "SKIP"}]}
        self.assertTrue(retention.test_failed(result))

    def test_legacy_test_failure_is_permanent(self) -> None:
        """Missing verdict fields in old results must not cause evidence loss."""
        result = {"tests": [{"name": "aer-native", "status": "failed"}]}
        self.assertTrue(retention.test_failed(result))
        self.assertTrue(retention.test_failed({"component_totals": {"kernel": {"failed": 1}}}))

    @staticmethod
    def add_run(store: str, now: datetime.datetime, age: int,
                run_id: int, tests: list[dict]) -> str:
        """Create a historical archive entry without new retention metadata."""
        created = now - datetime.timedelta(days=age)
        timestamp = created.strftime("%Y%m%dT%H%M%SZ")
        name = f"{timestamp}-{run_id}"
        directory = os.path.join(store, name)
        result_dir = os.path.join(directory, "daily", "x86_64")
        os.makedirs(result_dir)
        metadata = {"kind": "daily", "timestamp": timestamp, "created_at": created.isoformat(),
                    "conclusion": "failure", "url": f"https://example.test/{run_id}"}
        result = {"tests": tests, "features": [], "component_totals": {},
                  "infrastructure_failure": "VM stopped", "vm_status": "failed"}

        for path, data in ((os.path.join(directory, "daily.json"), metadata),
                           (os.path.join(result_dir, "result.json"), result)):
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(data, stream)

        with open(os.path.join(result_dir, "rasdaemon-source-revision"),
                  "w", encoding="utf-8") as stream:
            stream.write("a" * 40)

        with open(os.path.join(result_dir, "results.html"), "w", encoding="utf-8") as stream:
            stream.write("<h1>Kernel and rasdaemon results</h1>")

        return name

    def test_reclassifies_and_expires_old_infrastructure_only_runs(self) -> None:
        """Exercise real archive pruning and distinguish statuses on the public index."""
        now = datetime.datetime(2026, 11, 7, tzinfo=datetime.timezone.utc)
        infrastructure = [{"name": "guest", "status": "failed",
                           "kernel": "SKIP", "rasdaemon": "SKIP"}]
        regression = [{"name": "aer-native", "status": "failed", "kernel": "FAIL"}]

        with tempfile.TemporaryDirectory() as directory:
            store = os.path.join(directory, "store")
            site = os.path.join(directory, "site")
            old_infra = self.add_run(store, now, 31, 1, infrastructure)
            recent_infra = self.add_run(store, now, 29, 2, infrastructure)
            old_regression = self.add_run(store, now, 90, 3, regression + infrastructure)
            os.makedirs(os.path.join(site, old_infra))
            archive_results.archive(store, site, [], now)

            self.assertFalse(os.path.exists(os.path.join(store, old_infra)))
            self.assertFalse(os.path.exists(os.path.join(site, old_infra)))
            self.assertTrue(os.path.exists(os.path.join(store, recent_infra)))
            self.assertTrue(os.path.exists(os.path.join(store, old_regression)))
            with open(os.path.join(site, "archive", "index.html"), encoding="utf-8") as stream:
                page = stream.read()
            self.assertIn("test failure</a>", page)
            self.assertIn("infrastructure failure</a>", page)
            self.assertIn("Infrastructure-only failures", page)


if __name__ == "__main__":
    unittest.main()

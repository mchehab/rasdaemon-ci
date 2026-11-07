# SPDX-License-Identifier: GPL-2.0-only
"""Distinguish test regressions from infrastructure-only archived runs."""

import json
import os


SETUP_CHECKS = {"prerequisites", "payload", "configure", "build", "install",
                "install-payload", "tracefs", "debugfs", "guest", "coverage-contract"}
AGGREGATE_CHECKS = {"feature-coverage", "coverage-contract"}


def test_failed(result: dict) -> bool:
    """Preserve actual failures, including regressions before an infrastructure abort."""
    tests = result.get("tests", [])

    for test in tests:
        if test.get("status") != "failed" or test.get("name") in AGGREGATE_CHECKS:
            continue

        # A guest watchdog can identify a real kernel failure even when it
        # aborts the VM. Its explicit component verdict takes precedence.
        if "FAIL" in (test.get("kernel"), test.get("rasdaemon")):
            return True

        if test.get("name") in SETUP_CHECKS:
            continue

        verdicts = {test.get("kernel"), test.get("rasdaemon")}
        if result.get("infrastructure_failure") and verdicts <= {"SKIP", "N/A"}:
            continue

        # Historical tests may have no per-component verdict. Preserve them
        # rather than treating missing classification as proof of infra failure.
        return True

    if not tests:
        totals = result.get("component_totals", {}).values()
        return any(component.get("failed", 0) for component in totals)

    return False


def classify(directory: str, conclusion: str) -> str:
    """Reclassify retained raw evidence, including runs archived before this policy."""
    infrastructure = conclusion != "success"

    for root, _directories, filenames in os.walk(directory):
        if "result.json" not in filenames:
            continue

        try:
            with open(os.path.join(root, "result.json"), encoding="utf-8") as stream:
                result = json.load(stream)
        except (OSError, ValueError):
            # Do not delete unclassifiable evidence automatically.
            return "unknown"

        if test_failed(result):
            return "test_failure"

        infrastructure = infrastructure or bool(result.get("infrastructure_failure"))
        infrastructure = infrastructure or any(test.get("status") == "failed"
                                               for test in result.get("tests", []))

    return "infrastructure_failure" if infrastructure else "success"


def update_metadata(directory: str, metadata: list[dict]) -> None:
    """Persist classifications for both newly imported and historical archives."""
    for item in metadata:
        result_root = os.path.join(directory, item["kind"])
        item["failure_kind"] = classify(result_root, item["conclusion"])

        with open(os.path.join(directory, item["kind"] + ".json"),
                  "w", encoding="utf-8") as stream:
            json.dump(item, stream, indent=2)
            stream.write("\n")


def status(metadata: list[dict]) -> str:
    """Label the archive row without discarding the original workflow conclusion."""
    kinds = {item.get("failure_kind") for item in metadata}
    if "test_failure" in kinds:
        return "test failure"
    if "infrastructure_failure" in kinds:
        return "infrastructure failure"

    return "success" if all(item["conclusion"] == "success" for item in metadata) else "failure"

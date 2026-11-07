#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise failure and reuse paths from the workflow shell commands."""

import json
import os
import subprocess
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def workflow_text(name: str) -> str:
    """Load a workflow from the checkout under test."""
    with open(os.path.join(ROOT, ".github", "workflows", name), encoding="utf-8") as stream:
        return stream.read()


class PublicationTest(unittest.TestCase):
    """One usable architecture must survive a failure in its peer."""

    @staticmethod
    def publish(results: dict[str, str | None]) -> set[str]:
        """Execute the actual daily selection function with local artifact fixtures."""
        workflow = workflow_text("publish-results.yml")
        start = workflow.index("          find_daily()")
        function = workflow[start:workflow.index("          find_weekly()", start)]
        function = "\n".join(line[10:] for line in function.splitlines())

        with tempfile.TemporaryDirectory() as directory:
            artifacts = []

            for number, (arch, revision) in enumerate(results.items(), start=1):
                os.makedirs(os.path.join(directory, str(number)))
                artifacts.append({"id": number, "expired": False, "created_at": "2026-11-07",
                                  "name": "rasdaemon-qemu-results-" + arch})

                if revision:
                    path = os.path.join(directory, str(number), "rasdaemon-source-revision")

                    with open(path, "w", encoding="utf-8") as stream:
                        stream.write(revision)

                    path = os.path.join(directory, str(number), "result.json")

                    with open(path, "w", encoding="utf-8") as stream:
                        json.dump({"features": {"test": {}}, "component_totals": {"passed": 1}},
                                  stream)

            runs = {"workflow_runs": [{"id": 10, "status": "completed", "conclusion": "failure",
                                       "html_url": "https://example.test/run/10",
                                       "created_at": "2026-11-07"}]}
            environment = dict(os.environ, FIXTURES=directory, GITHUB_REPOSITORY="test/repo",
                               RUNS=json.dumps(runs),
                               ARTIFACTS=json.dumps({"artifacts": artifacts}))
            stubs = '''
set -euo pipefail
gh() {
  case "$*" in
    */artifacts*) printf '%s' "$ARTIFACTS" ;;
    *) printf '%s' "$RUNS" ;;
  esac
}
download_artifact() {
  mkdir -p "$2"
  cp -a "$FIXTURES/$1/." "$2/"
}
'''
            subprocess.run(["bash", "-c", stubs + function + '\nfind_daily daily ""'],
                           cwd=directory, env=environment, check=True,
                           capture_output=True, text=True)
            selected = os.path.join(directory, "build", "results", "daily")

            if not os.path.isdir(selected):
                return set()

            return set(os.listdir(selected)) - {"run"}

    def test_either_architecture_can_publish_alone(self):
        """A missing peer artifact must not suppress the successful report."""
        for arch in ("x86_64", "aarch64"):
            with self.subTest(arch=arch):
                self.assertEqual(self.publish({arch: "a" * 40}), {arch})

    def test_setup_failure_artifact_does_not_block_peer(self):
        """An upload containing no result.json is not a usable VM report."""
        self.assertEqual(self.publish({"x86_64": "a" * 40, "aarch64": None}), {"x86_64"})
        self.assertEqual(self.publish({"x86_64": None, "aarch64": "a" * 40}), {"aarch64"})

    def test_do_not_combine_different_source_revisions(self):
        """A single run's reports must refer to the same pinned source."""
        self.assertEqual(self.publish({"x86_64": "a" * 40, "aarch64": "b" * 40}), set())


class ImageReferenceTest(unittest.TestCase):
    """Docker's local digest must never escape as the published image reference."""

    def test_selects_registry_digest_even_when_local_digest_is_first(self):
        """Execute the workflow's jq filter against the failed-run digest ordering."""
        workflow = workflow_text("qemu-image.yml")
        start = workflow.index("'.[0].RepoDigests") + 1
        query = workflow[start:workflow.index("'", start)]
        registry = "ghcr.io/mchehab/rasdaemon-ci"
        expected = registry + "@sha256:" + "b" * 64
        data = [{"RepoDigests": ["rasdaemon-ci@sha256:" + "a" * 64, expected]}]
        result = subprocess.check_output(["jq", "-er", "--arg", "repository", registry, query],
                                         input=json.dumps(data), text=True)
        self.assertEqual(result.strip(), expected)

        data[0]["RepoDigests"].pop()
        result = subprocess.run(["jq", "-er", "--arg", "repository", registry, query],
                                input=json.dumps(data), text=True, check=False, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


class ArtifactTest(unittest.TestCase):
    """Cache misses and service failures have different meanings."""

    def test_exact_lookup_and_api_failure(self):
        """Only an unexpired exact match permits skipping compilation."""
        helper = os.path.join(ROOT, "tests", "qemu", "ci", "artifact.sh")

        with tempfile.TemporaryDirectory() as directory:
            executable = os.path.join(directory, "gh")

            with open(executable, "w", encoding="utf-8") as stream:
                stream.write('#!/bin/sh\nprintf "%s" "$RESPONSE"\nexit "$STATUS"\n')

            os.chmod(executable, 0o755)
            records = [{"id": 8, "name": "expected", "expired": True},
                       {"id": 9, "name": "expected-other", "expired": False},
                       {"id": 5, "name": "expected", "expired": False}]
            environment = dict(os.environ, PATH=directory + os.pathsep + os.environ["PATH"],
                               GITHUB_REPOSITORY="test/repo", STATUS="0",
                               RESPONSE=json.dumps([{"artifacts": records}]))
            result = subprocess.run([helper, "lookup", "expected"], env=environment,
                                    check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "5")
            result = subprocess.run([helper, "lookup", "missing"], env=environment,
                                    check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "")
            environment["STATUS"] = "1"
            result = subprocess.run([helper, "lookup", "expected"], env=environment,
                                    check=False, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

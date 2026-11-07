# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the publisher's artifact selection with partial architecture results."""

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest


class PartialPublicationTest(unittest.TestCase):
    """A missing or invalid peer must not hide a completed architecture."""

    # Keep the paired API and artifact fixtures together for these workflow checks.
    def check_partial_run(self, available_arch: str, invalid_peer: bool,  # pylint: disable=R0914
                          valid_peer: bool = False, status: str = "completed") -> None:
        """Run the workflow's real selection function against local API fixtures."""
        workflow = os.path.join(os.path.dirname(__file__), "..", ".github",
                                "workflows", "publish-results.yml")
        with open(workflow, encoding="utf-8") as stream:
            source = stream.read()

        start = source.index("          find_daily()")
        end = source.index("          find_weekly()", start)
        function = textwrap.dedent(source[start:end])

        with tempfile.TemporaryDirectory() as directory:
            fixtures = os.path.join(directory, "fixtures")
            os.makedirs(fixtures)
            artifacts = [{"id": 1, "name": "rasdaemon-qemu-results-" + available_arch,
                          "expired": False, "created_at": "2026-11-07T12:00:00Z"}]
            if invalid_peer or valid_peer:
                peer = "aarch64" if available_arch == "x86_64" else "x86_64"
                artifacts.append({"id": 2, "name": "rasdaemon-qemu-results-" + peer,
                                  "expired": False, "created_at": "2026-11-07T12:00:00Z"})

            data = {"workflow_runs": [{"id": 42, "html_url": "https://example.test/42",
                                       "conclusion": "failure", "status": status,
                                       "created_at": "2026-11-07T12:00:00Z"}]}
            for name, payload in (("runs.json", data),
                                  ("artifacts.json", {"artifacts": artifacts})):
                with open(os.path.join(fixtures, name), "w", encoding="utf-8") as stream:
                    json.dump(payload, stream)

            for artifact in artifacts:
                artifact_dir = os.path.join(fixtures, str(artifact["id"]))
                os.makedirs(artifact_dir)
                with open(os.path.join(artifact_dir, "rasdaemon-source-revision"),
                          "w", encoding="utf-8") as stream:
                    stream.write("a" * 40)
                result = {"component_totals": {"kernel": {}}, "features": ["feature"]}
                with open(os.path.join(artifact_dir, "result.json"),
                          "w", encoding="utf-8") as stream:
                    json.dump(result if artifact["id"] == 1 or valid_peer else {}, stream)
                with open(os.path.join(artifact_dir, "results.html"),
                          "w", encoding="utf-8") as stream:
                    stream.write("<h1>RAS Daemon feature CI tests</h1>")

            prelude = '''
            gh() {
              case "$2" in
                */42/artifacts*) cat fixtures/artifacts.json ;;
                *) cat fixtures/runs.json ;;
              esac
            }
            download_artifact() {
              mkdir -p "$2"
              cp -a "fixtures/$1/." "$2/"
            }
            '''
            script = textwrap.dedent(prelude) + function + '\nfind_daily daily ""\n'
            environment = dict(os.environ, GITHUB_REPOSITORY="example/repo")
            completed = subprocess.run(["bash", "-euxo", "pipefail"], input=script,
                                       text=True, cwd=directory, env=environment,
                                       capture_output=True, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            published = os.path.join(directory, "build", "results", "daily")
            if status != "completed":
                self.assertFalse(os.path.exists(published))
                return

            expected = [available_arch, "run"]
            if valid_peer:
                expected = ["x86_64", "aarch64", "run"]
            self.assertEqual(sorted(os.listdir(published)), sorted(expected))

    @unittest.skipUnless(shutil.which("jq"), "workflow selection requires jq")
    def test_arm_results_publish_without_x86(self) -> None:
        """A failed x86 image build must not discard ARM test results."""
        self.check_partial_run("aarch64", False)

    @unittest.skipUnless(shutil.which("jq"), "workflow selection requires jq")
    def test_x86_results_publish_without_arm(self) -> None:
        """Apply the same independence in the other direction."""
        self.check_partial_run("x86_64", False)

    @unittest.skipUnless(shutil.which("jq"), "workflow selection requires jq")
    def test_invalid_peer_does_not_discard_arm_results(self) -> None:
        """An incomplete peer artifact is excluded from the published inputs."""
        self.check_partial_run("aarch64", True)

    @unittest.skipUnless(shutil.which("jq"), "workflow selection requires jq")
    def test_both_architectures_publish_together(self) -> None:
        """A completed coordinator supplies both badge sets in one publication."""
        self.check_partial_run("aarch64", False, valid_peer=True)

    @unittest.skipUnless(shutil.which("jq"), "workflow selection requires jq")
    def test_running_coordinator_is_not_published(self) -> None:
        """An early ARM result must wait for the rest of the coordinator."""
        self.check_partial_run("aarch64", False, status="in_progress")


if __name__ == "__main__":
    unittest.main()

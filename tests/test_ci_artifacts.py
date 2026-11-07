# SPDX-License-Identifier: GPL-2.0-only
"""Exercise the real component cache lookup without GitHub or upstream builds."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "qemu"))
from ci import compilation  # pylint: disable=C0413


@unittest.skipUnless(shutil.which("jq"), "artifact lookup requires jq")
class ArtifactTest(unittest.TestCase):
    """SHA and effective options, rather than the CI commit, select builds."""

    def setUp(self) -> None:
        # unittest owns cleanup across setUp and the test body.
        self.temporary = tempfile.TemporaryDirectory()  # pylint: disable=R1732
        self.addCleanup(self.temporary.cleanup)
        self.directory = self.temporary.name
        self.revision = "a" * 40
        self.key = compilation.build_key(compilation.settings("qemu", "x86_64"))
        self.name = f"rasdaemon-qemu-x86_64-{self.revision}-{self.key}"
        root = os.path.dirname(os.path.dirname(__file__))
        workflow = os.path.join(root, ".github", "workflows", "component.yml")
        with open(workflow, encoding="utf-8") as stream:
            content = stream.read()
        body = content.split("        run: |\n", 1)[1].split("\n  build:", 1)[0]
        self.script = textwrap.dedent(body)
        destination = os.path.join(self.directory, "tests", "qemu", "ci")
        shutil.copytree(os.path.join(root, "tests", "qemu", "ci"), destination)
        executable = os.path.join(self.directory, "gh")
        with open(executable, "w", encoding="utf-8") as stream:
            stream.write('#!/bin/sh\ncase "$2" in\n'
                         '  */zip) cat "$ARCHIVE" ;;\n'
                         '  *) cat "$FIXTURE" ;;\n'
                         'esac\nexit "${API_STATUS:-0}"\n')
        os.chmod(executable, 0o755)

    def lookup(self, artifacts: list[dict], **overrides: str) -> subprocess.CompletedProcess:
        """Run the actual workflow step and shared lookup helper with fixture API data."""
        fixture = os.path.join(self.directory, "artifacts.json")
        with open(fixture, "w", encoding="utf-8") as stream:
            json.dump([{"artifacts": artifacts}], stream)
        environment = dict(os.environ, COMPONENT="qemu", ARCH="x86_64",
                           REVISION=self.revision, GITHUB_REPOSITORY="owner/repo",
                           GITHUB_OUTPUT=os.path.join(self.directory, "output"),
                           GITHUB_STEP_SUMMARY=os.path.join(self.directory, "summary"),
                           FIXTURE=fixture, PATH=self.directory + os.pathsep + os.environ["PATH"])
        environment.update(overrides)
        with open(environment["GITHUB_OUTPUT"], "w", encoding="utf-8"):
            pass
        return subprocess.run(["bash", "-e"], input=self.script, text=True,
                              cwd=self.directory, env=environment, capture_output=True, check=False)

    def outputs(self) -> dict[str, str]:
        """Read the step's selected artifact and name."""
        with open(os.path.join(self.directory, "output"), encoding="utf-8") as stream:
            return dict(line.strip().split("=", 1) for line in stream)

    def test_ci_sha_change_reuses_existing_build(self) -> None:
        """A harness push never changes the compiled artifact key."""
        artifact = {"id": 123, "name": self.name, "expired": False}
        for sha in ("b" * 40, "c" * 40):
            result = self.lookup([artifact], GITHUB_SHA=sha)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.outputs()["id"], "123")
            decision = self.outputs()["decision"]
            self.assertIn("SKIP compilation: qemu (x86_64)", decision)
            self.assertIn(self.revision, decision)
            self.assertIn(self.key, decision)
            self.assertIn("ID 123", decision)
            with open(os.path.join(self.directory, "summary"), encoding="utf-8") as stream:
                self.assertIn(decision, stream.read())

    def test_new_upstream_sha_misses_cache(self) -> None:
        """An older upstream build must not satisfy a new source revision."""
        result = self.lookup([{"id": 123, "name": self.name, "expired": False}],
                             REVISION="b" * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs()["id"], "")
        self.assertIn("BUILD required", self.outputs()["decision"])
        self.assertIn("b" * 40, self.outputs()["decision"])

    def test_legacy_kernel_tag_object_name_uses_commit_metadata(self) -> None:
        """Reuse tag-named archives only when their built commit matches the peeled SHA."""
        tag_object = "b" * 40
        name = "rasdaemon-kernel-x86_64-" + tag_object[:12]
        archive = os.path.join(self.directory, "legacy.zip")

        for actual in (self.revision, "c" * 40):
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("metadata/kernel.json", json.dumps({"commit": actual}))

            result = self.lookup([{"id": 123, "name": name, "expired": False}],
                                 COMPONENT="kernel", LEGACY_REVISION=tag_object, ARCHIVE=archive)
            self.assertEqual(result.returncode, 0, result.stderr)
            matched = actual == self.revision
            self.assertEqual(self.outputs()["id"], "123" if matched else "")
            self.assertIn("SKIP compilation" if matched else "BUILD required",
                          self.outputs()["decision"])
            self.assertIn(self.revision, self.outputs()["name"])
            shutil.rmtree(os.path.join(self.directory, "legacy"))

    def test_compilation_change_misses_cache(self) -> None:
        """Changed flags also bypass legacy builds with the original settings."""
        path = os.path.join(self.directory, "tests", "qemu", "ci", "compilation", "qemu.json")
        with open(path, encoding="utf-8") as stream:
            configuration = json.load(stream)
        configuration["common"]["configure"].append("--enable-debug")
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(configuration, stream)
        result = self.lookup([{"id": 123, "name": self.name, "expired": False}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs()["id"], "")
        self.assertNotEqual(self.outputs()["name"], self.name)
        self.assertIn("BUILD required", self.outputs()["decision"])
        self.assertIn("Legacy reuse is not eligible", result.stdout)

    def test_expired_artifact_is_not_reused(self) -> None:
        """Expired archives cannot satisfy a build dependency."""
        result = self.lookup([{"id": 123, "name": self.name, "expired": True}])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.outputs()["id"], "")
        self.assertIn("has expired", result.stderr)
        self.assertIn("BUILD required", self.outputs()["decision"])

    def test_missing_artifact_explains_rebuild(self) -> None:
        """A deleted or never-built artifact requires compilation and says why."""
        result = self.lookup([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no artifact named " + self.name, result.stderr)
        self.assertIn("no reusable artifact is available", self.outputs()["decision"])

    def test_api_failure_does_not_trigger_recompile(self) -> None:
        """A failed GitHub request fails the check instead of inventing a cache miss."""
        result = self.lookup([], API_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.outputs(), {})
        self.assertIn("Artifact lookup failed", result.stderr)

    def test_image_assembly_does_not_compile_missing_dependencies(self) -> None:
        """An incomplete explicitly supplied artifact fails before any upstream fetch."""
        script = os.path.join(self.directory, "tests", "qemu", "ci", "build-bundle.sh")
        for component in ("qemu", "kernel"):
            result = subprocess.run(["bash", script, "--" + component + "-dir", "missing"],
                                    cwd=self.directory, capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Supplied " + component + " artifact is missing metadata", result.stderr)
            self.assertNotIn("git fetch", result.stderr)


if __name__ == "__main__":
    unittest.main()

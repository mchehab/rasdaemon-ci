#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

import json
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.qemu import source_refs


class SourceRefsTest(unittest.TestCase):
    def test_selects_latest_final_release(self):
        refs = {"refs/tags/v6.17": "1" * 40,
                "refs/tags/v6.18-rc7": "2" * 40,
                "refs/tags/v6.18": "3" * 40,
                "refs/tags/v6.9": "4" * 40}
        self.assertEqual(source_refs.select_kernel_tag(refs, "release"),
                         "v6.18")

    def test_selects_latest_release_candidate(self):
        refs = {"refs/tags/v6.18-rc7": "1" * 40,
                "refs/tags/v6.19-rc1": "2" * 40,
                "refs/tags/v6.19": "3" * 40}
        self.assertEqual(source_refs.select_kernel_tag(refs, "rc"),
                         "v6.19-rc1")

    def test_latest_orders_candidates_and_final_releases(self):
        refs = {"refs/tags/v6.18": "1" * 40,
                "refs/tags/v6.19-rc2": "2" * 40,
                "refs/tags/v6.19-rc10": "3" * 40,
                "refs/tags/v6.18.1": "4" * 40,
                "refs/tags/v6.20-rc1^{}": "5" * 40}
        self.assertEqual(source_refs.select_kernel_tag(refs, "latest"), "v6.19-rc10")
        refs["refs/tags/v6.19"] = "6" * 40
        self.assertEqual(source_refs.select_kernel_tag(refs, "latest"), "v6.19")
        refs["refs/tags/v7.0-rc1"] = "7" * 40
        self.assertEqual(source_refs.select_kernel_tag(refs, "latest"), "v7.0-rc1")

    def test_latest_requires_a_matching_tag(self):
        with self.assertRaises(source_refs.SourceError):
            source_refs.select_kernel_tag({"refs/tags/v6.19.1": "1" * 40}, "latest")

    def test_resolve_ref_accepts_branch(self):
        with mock.patch.object(source_refs, "ls_remote", return_value={
                "refs/heads/master": "a" * 40}):
            self.assertEqual(
                source_refs.resolve_ref("example", "master"),
                ("a" * 40, "refs/heads/master"))

    def test_resolve_ref_peels_annotated_tag(self):
        with mock.patch.object(source_refs, "ls_remote", return_value={
                "refs/tags/v7.2": "a" * 40,
                "refs/tags/v7.2^{}": "b" * 40}):
            self.assertEqual(
                source_refs.resolve_ref("example", "refs/tags/v7.2"),
                ("b" * 40, "refs/tags/v7.2"))

    def test_exact_tags_peels_annotated_tags(self):
        refs = {"refs/tags/v1.0": "a" * 40,
                "refs/tags/v2.0": "b" * 40,
                "refs/tags/v2.0^{}": "a" * 40,
                "refs/tags/v3.0": "a" * 40,
                "refs/tags/v3.0^{}": "c" * 40}
        with mock.patch.object(source_refs, "ls_remote", return_value=refs):
            self.assertEqual(source_refs.exact_tags("example", "a" * 40),
                             ["v1.0", "v2.0"])

    def test_cached_image_metadata_resolves_actual_annotated_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args],
                                               text=True, stderr=subprocess.DEVNULL).strip()
            git("init")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.test")
            git("commit", "--allow-empty", "-m", "test")
            commit = git("rev-parse", "HEAD")
            git("tag", "-a", "v7.4-rc1", "-m", "release")
            metadata = os.path.join(directory, "kernel.json")
            with open(metadata, "w", encoding="utf-8") as stream:
                json.dump({"repository": directory, "commit": commit}, stream)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(source_refs.main(["tags-for-metadata", "--metadata", metadata]), 0)
            self.assertEqual(json.loads(output.getvalue())["tags"],
                             [git("describe", "--exact-match", "--tags", commit)])

    def test_describe_matches_git_for_tag_and_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args],
                                               text=True, stderr=subprocess.DEVNULL).strip()
            git("init")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.test")
            git("commit", "--allow-empty", "-m", "release")
            git("tag", "-a", "v7.4-rc1", "-m", "release")
            tagged = git("rev-parse", "HEAD")
            git("commit", "--allow-empty", "-m", "next")
            for commit in (tagged, git("rev-parse", "HEAD")):
                actual = source_refs.describe_commit(directory, commit)
                self.assertEqual(actual["describe"], git("describe", commit))

    def test_describe_uses_upstream_tags_for_fork_only_commit(self):
        """Describe the fork's commit using upstream tags, even with conflicting fork tags."""
        with tempfile.TemporaryDirectory() as directory:
            upstream = os.path.join(directory, "upstream")
            fork = os.path.join(directory, "fork")

            def git(repository, *args):
                return subprocess.check_output(["git", "-C", repository, *args],
                                               text=True, stderr=subprocess.DEVNULL).strip()

            os.mkdir(upstream)
            git(upstream, "init")
            git(upstream, "-c", "user.name=Test", "-c", "user.email=test@example.test",
                "commit", "--allow-empty", "-m", "release")
            git(upstream, "-c", "user.name=Test", "-c", "user.email=test@example.test",
                "tag", "-a", "v11.1.0", "-m", "release")
            git(directory, "clone", "--no-tags", upstream, fork)
            git(fork, "-c", "user.name=Test", "-c", "user.email=test@example.test",
                "commit", "--allow-empty", "-m", "fork changes")
            commit = git(fork, "rev-parse", "HEAD")
            metadata = os.path.join(directory, "qemu.json")

            with open(metadata, "w", encoding="utf-8") as stream:
                json.dump({"repository": fork, "commit": commit}, stream)

            for fork_has_tags in (False, True):
                if fork_has_tags:
                    git(fork, "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "tag", "-a", "v99.0.0", "-m", "fork-only tag")

                output = io.StringIO()

                with contextlib.redirect_stdout(output):
                    status = source_refs.main(["describe-metadata", "--metadata", metadata,
                                               "--tags-repository", upstream])

                self.assertEqual(status, 0)
                actual = json.loads(output.getvalue())
                self.assertEqual(actual["commit"], commit)
                self.assertRegex(actual["describe"], r"^v11\.1\.0-1-g" + commit[:7])
                self.assertEqual(actual["error"], "")


if __name__ == "__main__":
    unittest.main()

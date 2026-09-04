#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

import json
import pathlib
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

    def test_sync_updates_only_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "lock.json"
            path.write_text(json.dumps({
                "repository": "https://example.invalid/rasdaemon.git",
                "ref": "refs/heads/master", "commit": "a" * 40,
            }), encoding="utf-8")
            with mock.patch.object(source_refs, "resolve_ref",
                                   return_value=("b" * 40,
                                                 "refs/heads/master")):
                result = source_refs.sync_lock(
                    path, update=True, verify_ancestry=False)
            self.assertTrue(result["changed"])
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(value["commit"], "b" * 40)
            self.assertEqual(value["repository"],
                             "https://example.invalid/rasdaemon.git")


if __name__ == "__main__":
    unittest.main()

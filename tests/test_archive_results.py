# SPDX-License-Identifier: GPL-2.0-only
"""Verify the public evidence archive index."""

import unittest
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent / "qemu"))
from tests.qemu.archive_results import archive_index


class ArchiveIndexTest(unittest.TestCase):
    def test_table_is_newest_first_and_links_time_and_status_separately(self) -> None:
        retained = [
            ("20261027T035110Z-42", {
                "kind": "daily", "timestamp": "20261027T035110Z",
                "created_at": "2026-10-27T03:51:10+00:00", "conclusion": "success",
            }),
            ("20261028T035110Z-43", {
                "kind": "daily", "timestamp": "20261028T035110Z",
                "created_at": "2026-10-28T03:51:10+00:00", "conclusion": "failure",
            }),
            ("20261028T035110Z-43", {
                "kind": "weekly", "timestamp": "20261028T035110Z",
                "created_at": "2026-10-28T03:51:10+00:00", "conclusion": "failure",
            }),
        ]

        page = archive_index(retained)

        self.assertLess(page.index("2026-10-28 03:51:10 UTC"),
                        page.index("2026-10-27 03:51:10 UTC"))
        self.assertIn('href="../20261028T035110Z-43/daily/index.html">2026-10-28', page)
        self.assertIn('href="../20261028T035110Z-43/">failure</a>', page)
        self.assertIn('href="../20261027T035110Z-42/daily/index.html">2026-10-27', page)
        self.assertIn('href="../20261027T035110Z-42/">success</a>', page)
        self.assertNotIn("weekly", page)


if __name__ == "__main__":
    unittest.main()

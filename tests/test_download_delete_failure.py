from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ig_post_controller.database import Database
from ig_post_controller.services.account_service import AccountService
from ig_post_controller.services.download_service import DownloadService


class DownloadDeleteFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.database = Database(root / "synthetic.db")
        self.service = DownloadService(self.database)
        self.service.set_download_root(root / "downloads")
        self.folder = root / "downloads" / "synthetic-post"
        self.folder.mkdir()
        (self.folder / "meta.json").write_text(json.dumps({"shortcode": "synthetic"}), encoding="utf-8")
        (self.folder / "caption.txt").write_text("Synthetic caption", encoding="utf-8")
        account = AccountService(self.database).save_account(
            profile_url="https://www.instagram.com/synthetic/", username="synthetic",
            display_name="Synthetic", company_name="Synthetic",
        )
        with self.database.connect() as connection:
            self.post_id = connection.execute(
                """INSERT INTO posts (account_id, shortcode, caption, taken_at, post_type,
                    has_image, has_video, source_url, media_json, first_seen_at, last_refreshed_at)
                    VALUES (?, 'synthetic', 'Synthetic caption', '2026-10-12', 'image', 1, 0,
                    'https://www.instagram.com/p/synthetic/', '[]', '2026-10-12', '2026-10-12')""",
                (account.id,),
            ).lastrowid
            connection.execute(
                """INSERT INTO downloads (post_id, company_name, custom_title, folder_path,
                    media_json, caption_path, meta_path, downloaded_at, updated_at)
                    VALUES (?, 'Synthetic', 'Synthetic', ?, '[]', ?, ?, '2026-10-12', '2026-10-12')""",
                (self.post_id, str(self.folder), str(self.folder / "caption.txt"), str(self.folder / "meta.json")),
            )

    def tracking_count(self) -> int:
        with self.database.connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM downloads WHERE post_id = ?", (self.post_id,)).fetchone()[0]

    def test_file_removal_failure_preserves_download_tracking(self) -> None:
        with mock.patch.object(self.service, "_remove_path", side_effect=OSError("Synthetic file lock")):
            with self.assertRaisesRegex(OSError, "Synthetic file lock"):
                self.service.delete_downloaded_post(self.post_id)
        self.assertEqual(self.tracking_count(), 1)
        self.assertEqual((self.folder / "caption.txt").read_text(encoding="utf-8"), "Synthetic caption")

    def test_successful_removal_deletes_folder_and_tracking(self) -> None:
        self.assertTrue(self.service.delete_downloaded_post(self.post_id))
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.tracking_count(), 0)

    def test_unowned_folder_keeps_files_and_removes_only_tracking(self) -> None:
        (self.folder / "meta.json").write_text(json.dumps({"shortcode": "different"}), encoding="utf-8")
        self.assertTrue(self.service.delete_downloaded_post(self.post_id))
        self.assertTrue(self.folder.is_dir())
        self.assertEqual(self.tracking_count(), 0)

    def test_missing_download_record_remains_a_noop(self) -> None:
        self.assertFalse(self.service.delete_downloaded_post(self.post_id + 100))
        self.assertEqual(self.tracking_count(), 1)
        self.assertTrue(self.folder.is_dir())


if __name__ == "__main__":
    unittest.main()

"""Synthetic reproductions for docs/cloud-20261031-review.md.

Each test asserts the behavior observed on 2026-10-31 (the defect), so a
passing test means the issue still reproduces. No network: _download_binary
is patched to write synthetic bytes. All accounts/URLs are fake.

Run: QT_QPA_PLATFORM=offscreen python -m pytest -q review
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from ig_post_controller.database import Database
from ig_post_controller.models import MediaItem, PostRecord
from ig_post_controller.services.account_service import AccountService
from ig_post_controller.services.download_service import DownloadService
from ig_post_controller.utils.text import build_default_download_title, sanitize_for_path


def _insert_post(db: Database, account_id: int, shortcode: str, caption: str, media: list[MediaItem]) -> int:
    taken_at = datetime(2026, 4, 1, 9, 0, 0).isoformat()
    with db.connect() as connection:
        return connection.execute(
            """
            INSERT INTO posts (
                account_id, shortcode, caption, taken_at, post_type,
                has_image, has_video, thumbnail_url, source_url, media_json,
                first_seen_at, last_refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id, shortcode, caption, taken_at, "carousel", 1, 1,
                None, f"https://www.instagram.com/p/{shortcode}/",
                json.dumps([m.to_dict() for m in media]), taken_at, taken_at,
            ),
        ).lastrowid


def _post(db: Database, shortcode: str, caption: str, media: list[MediaItem]) -> PostRecord:
    accounts = AccountService(db).list_accounts()
    account = accounts[0] if accounts else AccountService(db).save_account(
        profile_url="https://www.instagram.com/synthetic_acct/",
        username="synthetic_acct",
        display_name="Synthetic",
        company_name="Synthetic Co",
    )
    post_id = _insert_post(db, account.id, shortcode, caption, media)
    return PostRecord(
        id=post_id, account_id=account.id, username=account.username,
        display_name=account.display_name, company_name=account.company_name,
        shortcode=shortcode, caption=caption, taken_at=datetime(2026, 4, 1, 9, 0, 0),
        post_type="carousel", has_image=True, has_video=True, thumbnail_url=None,
        source_url=f"https://www.instagram.com/p/{shortcode}/", media_items=media,
    )


def _fake_download(self, url: str, target_path: Path) -> None:
    target_path.write_bytes(b"synthetic:" + url.encode())


class ReviewRepro(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = Database(self.tmp / "app.db")
        self.service = DownloadService(self.db)
        self.service.set_download_root(self.tmp / "downloads")
        patcher = mock.patch.object(DownloadService, "_download_binary", _fake_download)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    # R1: media item without a usable URL is silently skipped, post is recorded as fully downloaded.
    def test_r1_missing_media_url_is_silently_skipped_and_recorded_as_complete(self) -> None:
        media = [
            MediaItem(index=0, media_type="image", remote_url="https://cdn.invalid/a.jpg"),
            MediaItem(index=1, media_type="video", remote_url=None, thumbnail_url="https://cdn.invalid/t.jpg"),
            MediaItem(index=2, media_type="image", remote_url="https://cdn.invalid/c.jpg"),
        ]
        post = _post(self.db, "SYNTH001", "carousel", media)
        result = self.service.download_post(post)

        files = sorted(p.name for p in Path(result.folder_path).iterdir())
        self.assertEqual(files, ["01.jpg", "03.jpg", "caption.txt", "meta.json"])  # 02 missing
        self.assertTrue(result.is_downloaded)
        self.assertEqual(len(result.media_items), 2)  # 3 expected
        listed = self.service.list_downloaded_posts()
        self.assertFalse(listed[0].download_folder_missing)  # UI shows it as healthy

    # R2: two distinct posts with the same date + caption prefix collide under the default layout.
    def test_r2_default_titles_collide_for_distinct_posts_same_day(self) -> None:
        caption = "Weekly special menu A"  # first 15 chars identical
        a = _post(self.db, "SYNTH00A", caption, [MediaItem(0, "image", "https://cdn.invalid/a.jpg")])
        b = _post(self.db, "SYNTH00B", caption + " (2)", [MediaItem(0, "image", "https://cdn.invalid/b.jpg")])
        self.assertEqual(
            build_default_download_title(a.taken_at, a.caption),
            build_default_download_title(b.taken_at, b.caption),
        )
        self.assertEqual(self.service.get_download_layout(), "organized")
        self.service.download_post(a)
        with mock.patch.object(DownloadService, "_download_binary", wraps=None) as dl:
            dl.side_effect = lambda url, path: path.write_bytes(b"x")
            with self.assertRaises(FileExistsError):
                self.service.download_post(b)
            self.assertEqual(dl.call_count, 1)  # media was fetched before the conflict was detected

    # R3: delete removes the DB row before files; a failing rmtree leaves an untracked folder.
    def test_r3_delete_failure_orphans_folder(self) -> None:
        post = _post(self.db, "SYNTH003", "x", [MediaItem(0, "image", "https://cdn.invalid/a.jpg")])
        result = self.service.download_post(post)
        with mock.patch("shutil.rmtree", side_effect=PermissionError("file in use (synthetic)")):
            with self.assertRaises(PermissionError):
                self.service.delete_downloaded_post(post.id)
        self.assertTrue(Path(result.folder_path).is_dir())
        self.assertEqual(self.service.list_downloaded_posts(), [])  # record gone, files remain

    # R4: sanitize_for_path leaves names Windows rejects or silently rewrites.
    def test_r4_sanitize_leaves_windows_unsafe_names(self) -> None:
        self.assertTrue(sanitize_for_path("a" * 100, max_length=20).endswith(".."))  # trailing dots
        self.assertEqual(sanitize_for_path("CON.txt"), "CON.txt")  # reserved device name w/ extension
        self.assertIn("\x01", sanitize_for_path("bad\x01name"))  # control char survives
        self.assertEqual(sanitize_for_path("COM0"), "COM0")

    # R5: file suffix is taken verbatim from the URL path (":" -> NTFS alternate data stream on Windows).
    def test_r5_media_suffix_taken_verbatim_from_url(self) -> None:
        self.assertEqual(DownloadService._suffix_for_media("https://cdn.invalid/x.jpg:ads", "image"), ".jpg:ads")
        self.assertEqual(len(DownloadService._suffix_for_media("https://cdn.invalid/x." + "a" * 300, "image")), 301)


if __name__ == "__main__":
    unittest.main()

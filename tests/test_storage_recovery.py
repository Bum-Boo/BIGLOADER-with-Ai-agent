from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest import mock

from ig_post_controller.database import Database
from ig_post_controller.models import MediaItem, PostRecord
from ig_post_controller.services.account_service import AccountService
from ig_post_controller.services.download_service import DownloadService


def create_post(db: Database, *, shortcode: str = "shortcode-1") -> PostRecord:
    account_service = AccountService(db)
    account = account_service.save_account(
        profile_url="https://www.instagram.com/clienta/",
        username="clienta",
        display_name="Client A",
        company_name="Client A",
    )
    taken_at = datetime(2026, 7, 1, 9, 0, 0)
    media_json = json.dumps(
        [
            {
                "index": 0,
                "media_type": "image",
                "remote_url": "https://example.com/media.jpg",
                "thumbnail_url": "https://example.com/thumb.jpg",
                "local_path": None,
                "local_thumbnail_path": None,
            }
        ]
    )
    with db.connect() as connection:
        post_id = connection.execute(
            """
            INSERT INTO posts (
                account_id, shortcode, caption, taken_at, post_type,
                has_image, has_video, thumbnail_url, source_url, media_json,
                first_seen_at, last_refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.id,
                shortcode,
                "Caption text",
                taken_at.isoformat(),
                "image",
                1,
                0,
                "https://example.com/thumb.jpg",
                f"https://www.instagram.com/p/{shortcode}/",
                media_json,
                taken_at.isoformat(),
                taken_at.isoformat(),
            ),
        ).lastrowid
    return PostRecord(
        id=post_id,
        account_id=account.id,
        username=account.username,
        display_name=account.display_name,
        company_name=account.company_name,
        shortcode=shortcode,
        caption="Caption text",
        taken_at=taken_at,
        post_type="image",
        has_image=True,
        has_video=False,
        thumbnail_url="https://example.com/thumb.jpg",
        source_url=f"https://www.instagram.com/p/{shortcode}/",
        media_items=[
            MediaItem(
                index=0,
                media_type="image",
                remote_url="https://example.com/media.jpg",
                thumbnail_url="https://example.com/thumb.jpg",
            )
        ],
    )


class StorageRecoveryTests(unittest.TestCase):
    def test_missing_configured_root_recovers_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db = Database(base / "test.db")
            missing = base / "disconnected-drive" / "downloads"
            fallback = base / "documents" / "BIGLOADEB Downloads"
            db.set_setting(DownloadService.DOWNLOAD_ROOT_SETTING, str(missing))
            service = DownloadService(db)

            with mock.patch(
                "ig_post_controller.services.download_service.get_default_download_root",
                return_value=fallback,
            ):
                recovered = service.get_download_root()

            self.assertEqual(recovered, fallback)
            self.assertTrue((fallback / service.STORAGE_MARKER_FILENAME).is_file())
            self.assertEqual(service.consume_download_root_recovery_notice(), str(missing))
            self.assertIn(missing, service.list_known_download_roots())
            self.assertEqual(service.consume_download_root_recovery_notice(), None)

    def test_flat_layout_places_post_folder_directly_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db = Database(base / "test.db")
            service = DownloadService(db)
            root = service.set_download_root(base / "downloads")
            service.set_download_layout("flat")
            post = create_post(db)
            service._download_binary = lambda url, target: target.write_bytes(b"image")  # type: ignore[method-assign]

            saved = service.download_post(post)
            folder = Path(saved.folder_path or "")

            self.assertEqual(folder.parent, root)
            self.assertIn(post.shortcode, folder.name)
            self.assertTrue((folder / "meta.json").is_file())

    def test_bulk_reconnect_finds_moved_post_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db = Database(base / "test.db")
            service = DownloadService(db)
            service.set_download_root(base / "downloads")
            post = create_post(db)
            service._download_binary = lambda url, target: target.write_bytes(b"image")  # type: ignore[method-assign]
            saved = service.download_post(post)
            original = Path(saved.folder_path or "")
            moved_root = base / "moved-library"
            moved_root.mkdir()
            moved = moved_root / original.name
            original.rename(moved)

            result = service.reconnect_downloads_under(moved_root)

            self.assertEqual(result["reconnected"], 1)
            reloaded = service.list_downloaded_posts()[0]
            self.assertEqual(Path(reloaded.folder_path or ""), moved)
            self.assertFalse(reloaded.download_folder_missing)
            self.assertTrue(service.will_delete_download_files(post.id or 0))

    def test_schema_migration_backs_up_once_and_preserves_existing_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "existing.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    PRAGMA user_version = 1;
                    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_url TEXT NOT NULL UNIQUE,
                        username TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL,
                        company_name TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_checked_at TEXT,
                        last_seen_post_shortcode TEXT
                    );
                    INSERT INTO accounts (
                        profile_url, username, display_name, company_name,
                        created_at, updated_at
                    ) VALUES (
                        'https://www.instagram.com/existing/', 'existing',
                        'Existing Account', 'Existing Company',
                        '2026-07-01T09:00:00', '2026-07-01T09:00:00'
                    );
                    """
                )

            migrated = Database(db_path)
            backups = list((base / "backups").glob("existing-before-schema-2-*.db"))

            self.assertEqual(len(backups), 1)
            with migrated.connect() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                account = connection.execute(
                    "SELECT username, archived FROM accounts WHERE username = 'existing'"
                ).fetchone()
            self.assertEqual(version, Database.SCHEMA_VERSION)
            self.assertEqual(account["username"], "existing")
            self.assertEqual(account["archived"], 0)

            Database(db_path)
            self.assertEqual(
                len(list((base / "backups").glob("existing-before-schema-2-*.db"))),
                1,
            )

    def test_removing_account_with_downloads_archives_it_without_losing_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db = Database(base / "test.db")
            service = DownloadService(db)
            service.set_download_root(base / "downloads")
            post = create_post(db)
            service._download_binary = lambda url, target: target.write_bytes(b"image")  # type: ignore[method-assign]
            service.download_post(post)
            account_service = AccountService(db)

            account_service.delete_account(post.account_id)

            self.assertEqual(account_service.list_accounts(), [])
            self.assertEqual(len(service.list_downloaded_posts()), 1)
            with db.connect() as connection:
                archived = connection.execute(
                    "SELECT archived FROM accounts WHERE id = ?", (post.account_id,)
                ).fetchone()
            self.assertEqual(archived["archived"], 1)


if __name__ == "__main__":
    unittest.main()

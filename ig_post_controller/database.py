from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
import shutil
from typing import Iterator


class Database:
    SCHEMA_VERSION = 2

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_before_migration()
        self._initialize()

    def _backup_before_migration(self) -> None:
        if not self.db_path.is_file() or self.db_path.stat().st_size == 0:
            return
        with closing(sqlite3.connect(self.db_path)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version >= self.SCHEMA_VERSION:
            return
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{self.db_path.stem}-before-schema-{self.SCHEMA_VERSION}-{stamp}{self.db_path.suffix}"
        shutil.copy2(self.db_path, backup_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_url TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_seen_post_shortcode TEXT,
                    archived INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    shortcode TEXT NOT NULL UNIQUE,
                    caption TEXT NOT NULL,
                    taken_at TEXT NOT NULL,
                    post_type TEXT NOT NULL,
                    has_image INTEGER NOT NULL,
                    has_video INTEGER NOT NULL,
                    thumbnail_url TEXT,
                    source_url TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_refreshed_at TEXT NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_posts_account_id ON posts(account_id);
                CREATE INDEX IF NOT EXISTS idx_posts_taken_at ON posts(taken_at);

                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL UNIQUE,
                    company_name TEXT NOT NULL,
                    custom_title TEXT NOT NULL,
                    folder_path TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    caption_path TEXT NOT NULL,
                    meta_path TEXT NOT NULL,
                    posted_to_cafe INTEGER NOT NULL DEFAULT 0,
                    downloaded_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_downloads_downloaded_at ON downloads(downloaded_at);
                """
            )
            account_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(accounts)").fetchall()
            }
            if "archived" not in account_columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    def get_setting(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

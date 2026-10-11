from __future__ import annotations

import json
import logging
import mimetypes
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from ig_post_controller.config import (
    DOWNLOAD_LAYOUT_SETTING_KEY,
    get_app_data_dir,
    get_default_download_root,
    normalize_download_layout,
)
from ig_post_controller.database import Database
from ig_post_controller.models import MediaItem, PostRecord
from ig_post_controller.utils.text import build_default_download_title, sanitize_for_path


logger = logging.getLogger(__name__)


class DownloadService:
    DOWNLOAD_ROOT_SETTING = "download_root"
    DOWNLOAD_ROOT_HISTORY_SETTING = "download_root_history"
    DOWNLOAD_ROOT_RECOVERY_SETTING = "download_root_recovery_notice"
    STORAGE_MARKER_FILENAME = ".bigloadeb-storage.json"

    def __init__(self, database: Database) -> None:
        self.database = database
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/132.0.0.0 Safari/537.36"
                )
            }
        )

    def get_download_root(self) -> Path:
        configured = self.database.get_setting(self.DOWNLOAD_ROOT_SETTING)
        if configured:
            path = Path(configured)
            if path.is_dir():
                try:
                    self._ensure_storage_marker(path)
                    return path
                except OSError:
                    logger.warning("Configured download root is not writable path=%s", path)
            return self._recover_unavailable_root(path)
        return self._activate_download_root(get_default_download_root(), remember_previous=False)

    def set_download_root(self, path: str | Path) -> Path:
        return self._activate_download_root(Path(path), remember_previous=True)

    def get_download_layout(self) -> str:
        value = normalize_download_layout(self.database.get_setting(DOWNLOAD_LAYOUT_SETTING_KEY))
        self.database.set_setting(DOWNLOAD_LAYOUT_SETTING_KEY, value)
        return value

    def set_download_layout(self, layout: str) -> str:
        normalized = normalize_download_layout(layout)
        self.database.set_setting(DOWNLOAD_LAYOUT_SETTING_KEY, normalized)
        return normalized

    def consume_download_root_recovery_notice(self) -> str | None:
        previous = self.database.get_setting(self.DOWNLOAD_ROOT_RECOVERY_SETTING)
        if previous:
            self.database.set_setting(self.DOWNLOAD_ROOT_RECOVERY_SETTING, "")
        return previous or None

    def list_known_download_roots(self) -> list[Path]:
        roots: list[Path] = []
        configured = self.database.get_setting(self.DOWNLOAD_ROOT_SETTING)
        if configured:
            roots.append(Path(configured))
        raw_history = self.database.get_setting(self.DOWNLOAD_ROOT_HISTORY_SETTING)
        if raw_history:
            try:
                payload = json.loads(raw_history)
            except json.JSONDecodeError:
                payload = []
            if isinstance(payload, list):
                roots.extend(Path(item) for item in payload if isinstance(item, str) and item)
        unique: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return unique

    def _activate_download_root(self, target: Path, *, remember_previous: bool) -> Path:
        previous = self.database.get_setting(self.DOWNLOAD_ROOT_SETTING)
        target.mkdir(parents=True, exist_ok=True)
        if not target.is_dir():
            raise NotADirectoryError(str(target))
        self._ensure_storage_marker(target)
        if remember_previous and previous and Path(previous) != target:
            self._remember_download_root(Path(previous))
        self.database.set_setting(self.DOWNLOAD_ROOT_SETTING, str(target))
        return target

    def _recover_unavailable_root(self, unavailable: Path) -> Path:
        self._remember_download_root(unavailable)
        candidates = [get_default_download_root(), get_app_data_dir() / "downloads"]
        last_error: OSError | None = None
        for candidate in candidates:
            try:
                recovered = self._activate_download_root(candidate, remember_previous=False)
            except OSError as exc:
                last_error = exc
                continue
            self.database.set_setting(self.DOWNLOAD_ROOT_RECOVERY_SETTING, str(unavailable))
            logger.warning("Download root unavailable; recovered old=%s new=%s", unavailable, recovered)
            return recovered
        if last_error is not None:
            raise last_error
        raise OSError("No writable download folder is available")

    def _remember_download_root(self, path: Path) -> None:
        history = [str(root) for root in self.list_known_download_roots() if root != path]
        history.insert(0, str(path))
        self.database.set_setting(
            self.DOWNLOAD_ROOT_HISTORY_SETTING,
            json.dumps(history[:20], ensure_ascii=False),
        )

    def _ensure_storage_marker(self, root: Path) -> None:
        marker = root / self.STORAGE_MARKER_FILENAME
        if marker.is_file():
            return
        payload = {
            "storage_id": uuid.uuid4().hex,
            "created_by": "BIGLOADEB",
            "created_at": datetime.now().isoformat(),
        }
        marker.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_downloaded_posts(
        self,
        *,
        account_id: int | None = None,
        media_filter: str = "all",
        sort_order: str = "newest",
    ) -> list[PostRecord]:
        clauses = ["d.id IS NOT NULL"]
        params: list[object] = []
        if account_id:
            clauses.append("a.id = ?")
            params.append(account_id)
        if media_filter == "image":
            clauses.append("p.has_image = 1")
        elif media_filter == "video":
            clauses.append("p.has_video = 1")
        order_by = "p.taken_at DESC" if sort_order == "newest" else "p.taken_at ASC"
        query = f"""
            SELECT
                p.*,
                a.username,
                a.display_name,
                a.company_name,
                d.id AS download_id,
                d.folder_path,
                d.custom_title,
                d.media_json AS download_media_json,
                d.posted_to_cafe,
                d.downloaded_at
            FROM posts p
            JOIN accounts a ON a.id = p.account_id
            JOIN downloads d ON d.post_id = p.id
            WHERE {" AND ".join(clauses)}
            ORDER BY {order_by}
        """
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_post(row) for row in rows]

    def set_posted_to_cafe(self, post_id: int, value: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE downloads
                SET posted_to_cafe = ?, updated_at = ?
                WHERE post_id = ?
                """,
                (1 if value else 0, datetime.now().isoformat(), post_id),
            )

    def will_delete_download_files(self, post_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.folder_path, p.shortcode
                FROM downloads d
                JOIN posts p ON p.id = d.post_id
                WHERE d.post_id = ?
                """,
                (post_id,),
            ).fetchone()
        if row is None or not row["folder_path"]:
            return False
        return self._is_owned_download_path(Path(row["folder_path"]), expected_shortcode=row["shortcode"])

    def list_stale_temporary_download_folders(self, *, max_age_seconds: int = 24 * 3600) -> list[Path]:
        root = self.get_download_root()
        cutoff = datetime.now().timestamp() - max_age_seconds
        stale: list[Path] = []
        for path in root.rglob(".*"):
            if not path.is_dir():
                continue
            name = path.name
            if ".tmp-" not in name and ".backup-" not in name:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    stale.append(path)
            except OSError:
                continue
        return sorted(stale)

    def reconnect_downloaded_post(self, post_id: int, folder_path: str | Path) -> bool:
        selected_folder = Path(folder_path)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.media_json, p.shortcode, p.source_url
                FROM downloads d
                JOIN posts p ON p.id = d.post_id
                WHERE d.post_id = ?
                """,
                (post_id,),
            ).fetchone()
            if row is None:
                return False
            shortcode = row["shortcode"]
            if not self._is_post_download_folder(selected_folder, expected_shortcode=shortcode, require_download_root=False):
                raise ValueError("Selected folder does not match this downloaded post.")
            meta_payload = self._read_meta_payload(selected_folder / "meta.json")
            post_url = meta_payload.get("post_url")
            if not post_url or post_url != row["source_url"]:
                raise ValueError("Selected folder belongs to a different post URL.")

            media_items = self._media_items_from_json(row["media_json"])
            if not media_items:
                raise ValueError("Downloaded media metadata is missing or invalid.")
            relocated_media_items = self._relocate_media_items(media_items, selected_folder)
            if relocated_media_items and not self._download_media_files_exist(relocated_media_items):
                raise ValueError("Selected folder is missing downloaded media files.")
            now = datetime.now().isoformat()
            connection.execute(
                """
                UPDATE downloads
                SET folder_path = ?,
                    caption_path = ?,
                    meta_path = ?,
                    media_json = ?,
                    updated_at = ?
                WHERE post_id = ?
                """,
                (
                    str(selected_folder),
                    str(selected_folder / "caption.txt"),
                    str(selected_folder / "meta.json"),
                    json.dumps([item.to_dict() for item in relocated_media_items], ensure_ascii=False),
                    now,
                    post_id,
                ),
            )
        return True

    def reconnect_downloads_under(self, folder_path: str | Path, *, max_meta_files: int = 5000) -> dict[str, int]:
        search_root = Path(folder_path)
        if not search_root.is_dir():
            raise ValueError("Selected folder is not available.")
        scanned = 0
        reconnected = 0
        skipped = 0
        for meta_path in search_root.rglob("meta.json"):
            if scanned >= max_meta_files:
                break
            if any(".tmp-" in part or ".backup-" in part for part in meta_path.parts):
                continue
            scanned += 1
            payload = self._read_meta_payload(meta_path)
            shortcode = payload.get("shortcode")
            post_url = payload.get("post_url")
            if not isinstance(shortcode, str) or not isinstance(post_url, str):
                skipped += 1
                continue
            with self.database.connect() as connection:
                row = connection.execute(
                    """
                    SELECT d.post_id, p.source_url
                    FROM downloads d
                    JOIN posts p ON p.id = d.post_id
                    WHERE p.shortcode = ?
                    """,
                    (shortcode,),
                ).fetchone()
            if row is None or row["source_url"] != post_url:
                skipped += 1
                continue
            try:
                if self.reconnect_downloaded_post(int(row["post_id"]), meta_path.parent):
                    reconnected += 1
                else:
                    skipped += 1
            except ValueError:
                skipped += 1
        if reconnected:
            self._remember_download_root(search_root)
        return {"scanned": scanned, "reconnected": reconnected, "skipped": skipped}

    def delete_downloaded_post(self, post_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT d.folder_path, p.shortcode
                FROM downloads d
                JOIN posts p ON p.id = d.post_id
                WHERE d.post_id = ?
                """,
                (post_id,),
            ).fetchone()
            if row is None:
                return False
            folder_path = row["folder_path"]
            shortcode = row["shortcode"]
            # A locked or unavailable folder must keep its tracking record so
            # the user can retry deletion after fixing the filesystem error.
            if folder_path:
                self._remove_owned_path(Path(folder_path), expected_shortcode=shortcode)
            connection.execute(
                "DELETE FROM downloads WHERE post_id = ?",
                (post_id,),
            )

        return True

    def download_post(
        self,
        post: PostRecord,
        *,
        title_override: str | None = None,
        batch_rule: str | None = None,
        batch_index: int | None = None,
    ) -> PostRecord:
        if post.id is None:
            raise ValueError("This post has not been saved locally yet, so it cannot be downloaded.")

        target_title = self._build_target_title(
            post,
            title_override=title_override,
            batch_rule=batch_rule,
            batch_index=batch_index,
        )
        root = self.get_download_root()
        if self.get_download_layout() == "flat":
            flat_name = sanitize_for_path(f"{target_title}_{post.shortcode}", max_length=120)
            target_dir = root / flat_name
        else:
            company_dir = root / sanitize_for_path(
                post.company_name or post.display_name or post.username,
                max_length=60,
            )
            target_dir = company_dir / "posts" / post.taken_at.strftime("%Y") / post.taken_at.strftime("%m") / target_title
        existing_folder = Path(post.folder_path) if post.folder_path else None
        temp_dir = target_dir.with_name(f".{target_dir.name}.tmp-{uuid.uuid4().hex}")
        temp_dir.mkdir(parents=True, exist_ok=False)

        try:
            local_media_items: list[MediaItem] = []
            for media in post.media_items:
                source = media.playable_source()
                if not source:
                    continue
                suffix = self._suffix_for_media(source, media.media_type)
                filename = f"{media.index + 1:02d}{suffix}"
                local_path = target_dir / filename
                temp_local_path = temp_dir / filename
                self._download_binary(source, temp_local_path)

                local_thumbnail_path = None
                if media.media_type == "video" and media.thumbnail_url:
                    thumbnail_filename = f"{media.index + 1:02d}_thumb.jpg"
                    local_thumbnail = target_dir / thumbnail_filename
                    temp_local_thumbnail = temp_dir / thumbnail_filename
                    self._download_binary(media.thumbnail_url, temp_local_thumbnail)
                    local_thumbnail_path = str(local_thumbnail)

                local_media_items.append(
                    MediaItem(
                        index=media.index,
                        media_type=media.media_type,
                        remote_url=media.remote_url,
                        thumbnail_url=media.thumbnail_url,
                        local_path=str(local_path),
                        local_thumbnail_path=local_thumbnail_path,
                    )
                )

            caption_path = target_dir / "caption.txt"
            (temp_dir / "caption.txt").write_text(post.caption or "", encoding="utf-8")

            meta_path = target_dir / "meta.json"
            meta_payload = {
                "shortcode": post.shortcode,
                "profile_username": post.username,
                "display_name": post.display_name,
                "company_name": post.company_name,
                "post_url": post.source_url,
                "post_type": post.post_type,
                "caption": post.caption,
                "taken_at": post.taken_at.isoformat(),
                "downloaded_at": datetime.now().isoformat(),
                "folder_title": target_title,
                "storage_layout": self.get_download_layout(),
                "media": [item.to_dict() for item in local_media_items],
            }
            (temp_dir / "meta.json").write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            self._remove_path(temp_dir)
            raise

        backup_dir: Path | None = None
        try:
            if target_dir.exists():
                if not self._is_owned_download_path(target_dir, expected_shortcode=post.shortcode):
                    raise FileExistsError(f"Target download folder already exists and is not owned by this post: {target_dir}")
                backup_dir = target_dir.with_name(f".{target_dir.name}.backup-{uuid.uuid4().hex}")
                target_dir.rename(backup_dir)
            temp_dir.rename(target_dir)
        except Exception:
            if not target_dir.exists() and backup_dir is not None and backup_dir.exists():
                backup_dir.rename(target_dir)
            self._remove_path(temp_dir)
            raise

        now = datetime.now().isoformat()
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO downloads (
                        post_id,
                        company_name,
                        custom_title,
                        folder_path,
                        media_json,
                        caption_path,
                        meta_path,
                        posted_to_cafe,
                        downloaded_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((
                        SELECT posted_to_cafe FROM downloads WHERE post_id = ?
                    ), 0), ?, ?)
                    ON CONFLICT(post_id) DO UPDATE SET
                        company_name = excluded.company_name,
                        custom_title = excluded.custom_title,
                        folder_path = excluded.folder_path,
                        media_json = excluded.media_json,
                        caption_path = excluded.caption_path,
                        meta_path = excluded.meta_path,
                        downloaded_at = excluded.downloaded_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        post.id,
                        post.company_name,
                        target_title,
                        str(target_dir),
                        json.dumps([item.to_dict() for item in local_media_items], ensure_ascii=False),
                        str(caption_path),
                        str(meta_path),
                        post.id,
                        now,
                        now,
                    ),
                )
        except Exception:
            self._remove_owned_path(target_dir, expected_shortcode=post.shortcode)
            if backup_dir is not None and backup_dir.exists() and not target_dir.exists():
                backup_dir.rename(target_dir)
            raise

        if backup_dir is not None:
            self._try_remove_owned_path(backup_dir, expected_shortcode=post.shortcode)
        if existing_folder and existing_folder.exists() and existing_folder != target_dir:
            self._try_remove_owned_path(existing_folder, expected_shortcode=post.shortcode)
        post.is_downloaded = True
        post.folder_path = str(target_dir)
        post.custom_title = target_title
        post.media_items = local_media_items
        post.downloaded_at = datetime.fromisoformat(now)
        return post

    def _remove_owned_path(self, path: Path, *, expected_shortcode: str | None = None) -> None:
        if not path.exists() or not self._is_owned_download_path(path, expected_shortcode=expected_shortcode):
            return
        self._remove_path(path)

    def _try_remove_owned_path(self, path: Path, *, expected_shortcode: str | None = None) -> None:
        try:
            self._remove_owned_path(path, expected_shortcode=expected_shortcode)
        except OSError as exc:
            logger.warning("Unable to remove owned download path path=%s error=%s", path, exc)

    def _is_owned_download_path(self, path: Path, *, expected_shortcode: str | None = None) -> bool:
        return self._is_post_download_folder(path, expected_shortcode=expected_shortcode, require_download_root=True)

    def _is_post_download_folder(
        self,
        path: Path,
        *,
        expected_shortcode: str | None = None,
        require_download_root: bool,
    ) -> bool:
        try:
            target = path.resolve(strict=False)
            if require_download_root:
                inside_known_root = False
                for known_root in self.list_known_download_roots():
                    root = known_root.resolve(strict=False)
                    try:
                        target.relative_to(root)
                    except ValueError:
                        continue
                    if target != root:
                        inside_known_root = True
                        break
                if not inside_known_root:
                    return False
        except OSError:
            return False
        meta_path = target / "meta.json"
        caption_path = target / "caption.txt"
        if not meta_path.is_file() or not caption_path.is_file():
            return False
        if expected_shortcode is None:
            return True
        payload = self._read_meta_payload(meta_path)
        return payload.get("shortcode") == expected_shortcode

    @staticmethod
    def _read_meta_payload(meta_path: Path) -> dict:
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _media_items_from_json(media_json: str | None) -> list[MediaItem]:
        if not media_json:
            return []
        try:
            payload = json.loads(media_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        items: list[MediaItem] = []
        for item in payload:
            if isinstance(item, dict):
                items.append(MediaItem.from_dict(item))
        return items

    @staticmethod
    def _download_media_files_exist(media_items: list[MediaItem]) -> bool:
        expected_paths = [Path(item.local_path) for item in media_items if item.local_path]
        expected_paths.extend(Path(item.local_thumbnail_path) for item in media_items if item.local_thumbnail_path)
        return bool(expected_paths) and all(path.is_file() for path in expected_paths)

    @staticmethod
    def _relocate_media_items(media_items: list[MediaItem], folder_path: Path) -> list[MediaItem]:
        relocated: list[MediaItem] = []
        for media in media_items:
            local_path = None
            if media.local_path:
                local_path = str(folder_path / Path(media.local_path).name)
            local_thumbnail_path = None
            if media.local_thumbnail_path:
                local_thumbnail_path = str(folder_path / Path(media.local_thumbnail_path).name)
            relocated.append(
                MediaItem(
                    index=media.index,
                    media_type=media.media_type,
                    remote_url=media.remote_url,
                    thumbnail_url=media.thumbnail_url,
                    local_path=local_path,
                    local_thumbnail_path=local_thumbnail_path,
                )
            )
        return relocated

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    @staticmethod
    def _build_target_title(
        post: PostRecord,
        *,
        title_override: str | None,
        batch_rule: str | None,
        batch_index: int | None,
    ) -> str:
        if title_override:
            return sanitize_for_path(title_override, max_length=80)
        if batch_rule:
            base = sanitize_for_path(batch_rule, max_length=72)
            if batch_index is None:
                return base
            return sanitize_for_path(f"{base}_{batch_index:02d}", max_length=80)
        return build_default_download_title(post.taken_at, post.caption)

    def _download_binary(self, url: str, target_path: Path) -> None:
        response = self.session.get(url, timeout=60, stream=True)
        response.raise_for_status()
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    handle.write(chunk)

    @staticmethod
    def _suffix_for_media(url: str, media_type: str) -> str:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix
        if suffix:
            return suffix
        if media_type == "video":
            return ".mp4"
        guessed = mimetypes.guess_extension(mimetypes.guess_type(url)[0] or "")
        return guessed or ".jpg"

    def _row_to_post(self, row) -> PostRecord:
        media_items = self._media_items_from_json(row["download_media_json"])
        media_json_broken = bool(row["download_media_json"] and not media_items)
        folder_path = row["folder_path"]
        download_folder_missing = media_json_broken or not self._is_post_download_folder(
            Path(folder_path),
            expected_shortcode=row["shortcode"],
            require_download_root=False,
        )
        return PostRecord(
            id=row["id"],
            account_id=row["account_id"],
            username=row["username"],
            display_name=row["display_name"],
            company_name=row["company_name"],
            shortcode=row["shortcode"],
            caption=row["caption"],
            taken_at=datetime.fromisoformat(row["taken_at"]),
            post_type=row["post_type"],
            has_image=bool(row["has_image"]),
            has_video=bool(row["has_video"]),
            thumbnail_url=media_items[0].preview_source(prefer_local=True) if media_items else None,
            source_url=row["source_url"],
            media_items=media_items,
            is_downloaded=True,
            folder_path=folder_path,
            custom_title=row["custom_title"],
            posted_to_cafe=bool(row["posted_to_cafe"]),
            downloaded_at=datetime.fromisoformat(row["downloaded_at"]) if row["downloaded_at"] else None,
            download_id=row["download_id"],
            download_folder_missing=download_folder_missing,
        )

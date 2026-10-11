from __future__ import annotations

import json
import re
import threading
import logging
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

import requests

from ig_post_controller.config import FETCH_LIMIT, REMOTE_REQUEST_TIMEOUT_SECONDS, STARTUP_CHECK_LIMIT
from ig_post_controller.database import Database
from ig_post_controller.models import MediaItem, NewPostCheckResult, PostRecord
from ig_post_controller.services.account_service import AccountService
from ig_post_controller.utils.text import extract_username_from_input, merge_media_items

try:
    import instaloader
except ImportError:  # pragma: no cover - optional fallback only
    instaloader = None


logger = logging.getLogger(__name__)


class InstagramAccessError(RuntimeError):
    """Raised when Instagram refuses or limits unauthenticated profile/feed access."""


class InstagramRateLimitError(InstagramAccessError):
    """Raised when Instagram rate-limits a profile/feed request."""


class InstagramService:
    WEB_APP_ID = "936619743392459"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    )

    def __init__(self, database: Database, account_service: AccountService) -> None:
        self.database = database
        self.account_service = account_service
        self.session = requests.Session()
        self._session_lock = threading.Lock()
        self.session.headers.update({"User-Agent": self.USER_AGENT})
        self.loader = None
        self.context = None
        if instaloader is not None:
            self.loader = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                quiet=True,
            )
            self.context = self.loader.context

    def resolve_profile(self, profile_input: str) -> dict[str, str]:
        username = extract_username_from_input(profile_input)

        page = self._fetch_profile_page(username)
        if page.status_code == 404:
            raise ValueError(f"Profile '{username}' was not found on Instagram.")
        try:
            self._raise_for_status(page)
        except InstagramAccessError:
            logger.warning("Instagram profile access limited username=%s status=%s", username, page.status_code)
            return self._fallback_profile(username)

        try:
            feed_items = self._fetch_feed_items(username, count=1)
        except InstagramAccessError:
            logger.warning("Instagram feed access limited while resolving profile username=%s", username)
            feed_items = []
        if feed_items:
            user = feed_items[0].get("user") or {}
            canonical_username = user.get("username") or username
            display_name = user.get("full_name") or canonical_username
        else:
            canonical_username = username
            display_name = self._extract_display_name_from_html(page.text, username)

        return {
            "profile_url": f"https://www.instagram.com/{canonical_username}/",
            "username": canonical_username,
            "display_name": display_name,
        }

    def initial_sync_account(self, account_id: int, limit: int = FETCH_LIMIT) -> list[PostRecord]:
        account = self.account_service.get_account(account_id)
        if not account:
            return []
        return self._sync_account(account, limit=limit, baseline=True)[0]

    def refresh_account_posts(self, account_id: int, limit: int = FETCH_LIMIT) -> list[PostRecord]:
        account = self.account_service.get_account(account_id)
        if not account:
            return []
        posts, _ = self._sync_account(account, limit=limit, baseline=False)
        return posts

    def refresh_all_accounts(self, limit: int = FETCH_LIMIT) -> list[PostRecord]:
        all_posts: list[PostRecord] = []
        for account in self.account_service.list_accounts():
            try:
                posts, _ = self._sync_account(account, limit=limit, baseline=False)
            except InstagramAccessError:
                logger.warning("Instagram access limited during refresh_all username=%s", account.username)
                continue
            all_posts.extend(posts)
        return sorted(all_posts, key=lambda post: post.taken_at, reverse=True)

    def check_for_new_posts(self, limit: int = STARTUP_CHECK_LIMIT) -> NewPostCheckResult:
        new_posts: list[PostRecord] = []
        accounts = self.account_service.list_accounts()
        for account in accounts:
            try:
                _, account_new_posts = self._sync_account(
                    account,
                    limit=limit,
                    baseline=account.last_seen_post_shortcode is None,
                )
            except InstagramAccessError:
                logger.warning("Instagram access limited during startup check username=%s", account.username)
                continue
            new_posts.extend(account_new_posts)
        new_posts.sort(key=lambda post: post.taken_at, reverse=True)
        return NewPostCheckResult(new_posts=new_posts, checked_accounts=len(accounts))

    def get_cached_posts(
        self,
        *,
        account_id: int | None = None,
        media_filter: str = "all",
        sort_order: str = "newest",
    ) -> list[PostRecord]:
        clauses = ["1 = 1"]
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
            LEFT JOIN downloads d ON d.post_id = p.id
            WHERE {" AND ".join(clauses)}
            ORDER BY {order_by}
        """
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_post(row) for row in rows]

    def get_post_by_shortcode(self, shortcode: str) -> PostRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
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
                LEFT JOIN downloads d ON d.post_id = p.id
                WHERE p.shortcode = ?
                """,
                (shortcode,),
            ).fetchone()
        return self._row_to_post(row) if row else None

    def refresh_post(self, shortcode: str) -> PostRecord:
        existing = self.get_post_by_shortcode(shortcode)
        if not existing:
            raise ValueError("That post is not registered in the local database yet.")

        try:
            items = self._fetch_feed_items(existing.username, count=max(FETCH_LIMIT, 36))
            refreshed = next(
                self._item_to_post_record(existing.account_id, existing.company_name, item)
                for item in items
                if item.get("code") == shortcode
            )
            self._upsert_posts(existing.account_id, [refreshed])
            return self.get_post_by_shortcode(shortcode) or refreshed
        except Exception:
            if self.loader is not None and self.context is not None:
                try:
                    post = instaloader.Post.from_shortcode(self.context, shortcode)
                    refreshed = self._instaloader_post_to_record(
                        existing.account_id,
                        existing.username,
                        existing.display_name,
                        existing.company_name,
                        post,
                    )
                    self._upsert_posts(existing.account_id, [refreshed])
                    return self.get_post_by_shortcode(shortcode) or refreshed
                except Exception:
                    return existing
            return existing

    def _sync_account(self, account, *, limit: int, baseline: bool) -> tuple[list[PostRecord], list[PostRecord]]:
        logger.info("Instagram sync started username=%s limit=%s baseline=%s", account.username, limit, baseline)
        items = self._fetch_feed_items(account.username, count=limit)
        if not items and self.loader is not None and self.context is not None:
            items = self._fetch_feed_items_with_instaloader(account.username, count=limit)

        posts = [self._item_to_post_record(account.id, account.company_name, item) for item in items]
        self._upsert_posts(account.id, posts)

        new_posts: list[PostRecord] = []
        if not baseline and account.last_seen_post_shortcode:
            for post in posts:
                if post.shortcode == account.last_seen_post_shortcode:
                    break
                new_posts.append(post)

        header_user = (items[0].get("user") or {}) if items else {}
        last_seen_shortcode = posts[0].shortcode if posts else account.last_seen_post_shortcode
        self.account_service.update_last_checked(
            account.id,
            last_checked_at=datetime.now(),
            last_seen_post_shortcode=last_seen_shortcode,
            display_name=header_user.get("full_name") or account.display_name,
        )
        logger.info(
            "Instagram sync finished username=%s posts=%s new_posts=%s",
            account.username,
            len(posts),
            len(new_posts),
        )
        return posts, new_posts

    def _fetch_profile_page(self, username: str) -> requests.Response:
        with self._session_lock:
            return self.session.get(
                f"https://www.instagram.com/{username}/",
                headers={"Referer": "https://www.instagram.com/"},
                timeout=REMOTE_REQUEST_TIMEOUT_SECONDS,
            )

    def _fetch_feed_items(self, username: str, *, count: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        next_max_id: str | None = None
        requested_cursors: set[str | None] = set()

        while len(items) < count:
            if next_max_id in requested_cursors:
                break
            requested_cursors.add(next_max_id)
            page_items, next_max_id = self._fetch_feed_page(username, next_max_id=next_max_id)
            if not page_items:
                break
            for item in page_items:
                code = item.get("code")
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                items.append(item)
                if len(items) >= count:
                    break
            if not next_max_id:
                break
        return items

    def _fetch_feed_page(self, username: str, *, next_max_id: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        params = {"count": 12}
        if next_max_id:
            params["max_id"] = next_max_id
        with self._session_lock:
            response = self.session.get(
                f"https://www.instagram.com/api/v1/feed/user/{username}/username/",
                headers={
                    "Referer": f"https://www.instagram.com/{username}/",
                    "X-IG-App-ID": self.WEB_APP_ID,
                    "X-Requested-With": "XMLHttpRequest",
                },
                params=params,
                timeout=REMOTE_REQUEST_TIMEOUT_SECONDS,
            )
        self._raise_for_status(response)
        payload = response.json()
        return payload.get("items") or [], payload.get("next_max_id")

    def _fetch_feed_items_with_instaloader(self, username: str, *, count: int) -> list[dict[str, Any]]:
        if self.context is None:
            return []
        profile = instaloader.Profile.from_username(self.context, username)
        posts = []
        for post in profile.get_posts():
            posts.append(self._instaloader_post_to_api_like_dict(post, profile.username, profile.full_name or profile.username))
            if len(posts) >= count:
                break
        return posts

    def _upsert_posts(self, account_id: int, posts: list[PostRecord]) -> None:
        now = datetime.now().isoformat()
        with self.database.connect() as connection:
            for post in posts:
                connection.execute(
                    """
                    INSERT INTO posts (
                        account_id,
                        shortcode,
                        caption,
                        taken_at,
                        post_type,
                        has_image,
                        has_video,
                        thumbnail_url,
                        source_url,
                        media_json,
                        first_seen_at,
                        last_refreshed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(shortcode) DO UPDATE SET
                        account_id = excluded.account_id,
                        caption = excluded.caption,
                        taken_at = excluded.taken_at,
                        post_type = excluded.post_type,
                        has_image = excluded.has_image,
                        has_video = excluded.has_video,
                        thumbnail_url = excluded.thumbnail_url,
                        source_url = excluded.source_url,
                        media_json = excluded.media_json,
                        last_refreshed_at = excluded.last_refreshed_at
                    """,
                    (
                        account_id,
                        post.shortcode,
                        post.caption,
                        post.taken_at.isoformat(),
                        post.post_type,
                        1 if post.has_image else 0,
                        1 if post.has_video else 0,
                        post.thumbnail_url,
                        post.source_url,
                        json.dumps([item.to_dict() for item in post.media_items], ensure_ascii=False),
                        now,
                        now,
                    ),
                )

    def _item_to_post_record(self, account_id: int, company_name: str, item: dict[str, Any]) -> PostRecord:
        user = item.get("user") or {}
        username = user.get("username") or ""
        display_name = user.get("full_name") or username
        media_items = self._extract_media_items(item)

        has_image = any(media.media_type == "image" for media in media_items)
        has_video = any(media.media_type == "video" for media in media_items)
        post_type = self._detect_post_type(item)
        shortcode = item.get("code") or ""

        return PostRecord(
            id=None,
            account_id=account_id,
            username=username,
            display_name=display_name,
            company_name=company_name,
            shortcode=shortcode,
            caption=((item.get("caption") or {}).get("text") or ""),
            taken_at=datetime.fromtimestamp(int(item.get("taken_at") or 0), tz=timezone.utc).replace(tzinfo=None),
            post_type=post_type,
            has_image=has_image,
            has_video=has_video,
            thumbnail_url=media_items[0].thumbnail_url if media_items else None,
            source_url=self._build_post_url(shortcode, post_type),
            media_items=media_items,
        )

    def _extract_media_items(self, item: dict[str, Any]) -> list[MediaItem]:
        if item.get("carousel_media"):
            media_items: list[MediaItem] = []
            for index, media in enumerate(item.get("carousel_media") or []):
                media_items.append(
                    MediaItem(
                        index=index,
                        media_type="video" if self._is_video(media) else "image",
                        remote_url=self._best_video_url(media) if self._is_video(media) else self._best_image_url(media),
                        thumbnail_url=self._best_image_url(media),
                    )
                )
            return media_items

        return [
            MediaItem(
                index=0,
                media_type="video" if self._is_video(item) else "image",
                remote_url=self._best_video_url(item) if self._is_video(item) else self._best_image_url(item),
                thumbnail_url=self._best_image_url(item),
            )
        ]

    @staticmethod
    def _detect_post_type(item: dict[str, Any]) -> str:
        if item.get("carousel_media"):
            return "carousel"
        return "video" if InstagramService._is_video(item) else "image"

    @staticmethod
    def _is_video(item: dict[str, Any]) -> bool:
        return bool(item.get("video_versions")) or item.get("media_type") == 2

    @staticmethod
    def _best_image_url(item: dict[str, Any]) -> str | None:
        candidates = ((item.get("image_versions2") or {}).get("candidates") or [])
        if not candidates:
            return None
        best = max(candidates, key=lambda candidate: (candidate.get("width", 0) * candidate.get("height", 0)))
        return best.get("url")

    @staticmethod
    def _best_video_url(item: dict[str, Any]) -> str | None:
        versions = item.get("video_versions") or []
        if not versions:
            return None
        best = max(versions, key=lambda version: (version.get("width", 0) * version.get("height", 0), version.get("type", 0)))
        return best.get("url")

    @staticmethod
    def _build_post_url(shortcode: str, post_type: str) -> str:
        if post_type == "video":
            return f"https://www.instagram.com/reel/{shortcode}/"
        return f"https://www.instagram.com/p/{shortcode}/"

    @staticmethod
    def _fallback_profile(username: str) -> dict[str, str]:
        return {
            "profile_url": f"https://www.instagram.com/{username}/",
            "username": username,
            "display_name": username,
        }

    @staticmethod
    def _raise_for_status(response: requests.Response) -> None:
        if InstagramService._is_rate_limited_response(response):
            raise InstagramRateLimitError(
                "Instagram is temporarily limiting requests for this profile/feed. Try refreshing later."
            )
        if InstagramService._is_access_limited_response(response):
            raise InstagramAccessError(
                "Instagram refused or limited access to this profile/feed. Try refreshing later."
            )
        response.raise_for_status()

    @staticmethod
    def _is_rate_limited_response(response: requests.Response) -> bool:
        return response.status_code == 429

    @staticmethod
    def _is_unauthorized_response(response: requests.Response) -> bool:
        return response.status_code in {401, 403}

    @staticmethod
    def _is_login_redirect_response(response: requests.Response) -> bool:
        return "/accounts/login" in (response.url or "")

    @staticmethod
    def _is_access_limited_response(response: requests.Response) -> bool:
        return (
            InstagramService._is_rate_limited_response(response)
            or InstagramService._is_unauthorized_response(response)
            or InstagramService._is_login_redirect_response(response)
        )

    @staticmethod
    def _extract_display_name_from_html(html: str, username: str) -> str:
        match = re.search(r'property="og:title" content="([^"]+)"', html)
        if not match:
            return username
        title = unescape(match.group(1))
        display_name = title.split(" (@", 1)[0].strip()
        return display_name or username

    def _instaloader_post_to_api_like_dict(self, post, username: str, display_name: str) -> dict[str, Any]:
        item: dict[str, Any] = {
            "code": post.shortcode,
            "taken_at": int(post.date_utc.replace(tzinfo=timezone.utc).timestamp()),
            "caption": {"text": post.caption or ""},
            "user": {"username": username, "full_name": display_name},
            "media_type": 2 if post.is_video else 1,
        }
        if post.typename == "GraphSidecar":
            carousel_media = []
            for node in post.get_sidecar_nodes():
                carousel_media.append(
                    {
                        "media_type": 2 if node.is_video else 1,
                        "video_versions": [{"url": node.video_url, "width": 0, "height": 0}] if node.is_video else [],
                        "image_versions2": {"candidates": [{"url": node.display_url, "width": 0, "height": 0}]},
                    }
                )
            item["carousel_media"] = carousel_media
        elif post.is_video:
            item["video_versions"] = [{"url": post.video_url, "width": 0, "height": 0}]
            item["image_versions2"] = {"candidates": [{"url": post.url, "width": 0, "height": 0}]}
        else:
            item["image_versions2"] = {"candidates": [{"url": post.url, "width": 0, "height": 0}]}
        return item

    def _instaloader_post_to_record(
        self,
        account_id: int,
        username: str,
        display_name: str,
        company_name: str,
        post,
    ) -> PostRecord:
        return self._item_to_post_record(
            account_id,
            company_name,
            self._instaloader_post_to_api_like_dict(post, username, display_name),
        )

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
    def _is_download_folder_for_post(path: Path, *, expected_shortcode: str) -> bool:
        meta_path = path / "meta.json"
        caption_path = path / "caption.txt"
        if not meta_path.is_file() or not caption_path.is_file():
            return False
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("shortcode") == expected_shortcode

    def _row_to_post(self, row) -> PostRecord:
        remote_items = self._media_items_from_json(row["media_json"])
        local_items = self._media_items_from_json(row["download_media_json"])
        media_items = merge_media_items(remote_items, local_items)
        download_folder_missing = False
        if row["download_id"]:
            download_folder_missing = not self._is_download_folder_for_post(
                Path(row["folder_path"]),
                expected_shortcode=row["shortcode"],
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
            thumbnail_url=row["thumbnail_url"],
            source_url=row["source_url"],
            media_items=media_items,
            is_downloaded=bool(row["download_id"]),
            folder_path=row["folder_path"],
            custom_title=row["custom_title"],
            posted_to_cafe=bool(row["posted_to_cafe"]) if row["posted_to_cafe"] is not None else False,
            downloaded_at=datetime.fromisoformat(row["downloaded_at"]) if row["downloaded_at"] else None,
            download_id=row["download_id"],
            download_folder_missing=download_folder_missing,
        )

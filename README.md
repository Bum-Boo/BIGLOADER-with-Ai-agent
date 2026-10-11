# BIGLOADEB

> Account-first Instagram post collection and local media management for Windows.

[English](README.md) | [한국어](docs/README.ko.md) | [日本語](docs/README.ja.md) | [中文](docs/README.zh-CN.md)

BIGLOADEB is a Windows desktop app for collecting and managing public Instagram posts from multiple business/client accounts.

It is designed for older, non-technical staff and focuses on a simple account-first workflow, clear feeds, and predictable local folder organization.

![Combined feed of the registered accounts, each post with a download button](docs/demo-screenshots/bigloadeb-use-02-feed-result.png)

## Safety and privacy

- Use the app only for public Instagram content that you are authorized to access and download.
- BIGLOADEB does not ask for or store Instagram passwords and does not upload posts.
- Settings, the SQLite database, thumbnails, logs, and downloaded media remain on the Windows computer unless you move or share them.
- Instagram may rate-limit or block public access. The app does not bypass platform controls; stop and retry later rather than attempting to evade a limit.
- Review captions and media before sharing them, and keep personal or client-sensitive material out of public screenshots and bug reports.

## Features

- Register public Instagram profile URLs manually
- Start on an account list screen with username and display name
- Check registered accounts for new posts on app launch
- View a combined online feed or a per-account feed
- Filter by image-only or video-only posts
- Sort by date
- Open a post detail view with carousel support, caption copy, and media preview
- Download posts into account-based local folders
- Track downloaded posts in SQLite
- Mark downloaded posts as `posted to cafe`
- Delete downloaded posts from local storage and local tracking
- Switch language and theme from Settings

## Project Layout

- `ig_post_controller/` - app source code
- `tests/` - regression and smoke tests
- `build.ps1` - Windows release build script
- `build_debug.ps1` - console debug build
- `build_debug_onefile.ps1` - console one-file debug build
- `IGPostController.spec` - PyInstaller spec file

## Development Setup

Requirements: Windows, Python 3, and network access to public Instagram pages. The dependency set is declared in `requirements.txt`.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run in Dev Mode

```powershell
python -m ig_post_controller
```

## Windows Release

### Download

1. Open the GitHub Releases page for this repository.
2. Download the latest Windows release asset.
3. Extract the archive if the release is packaged as a `.zip`.
4. Run `IGPostController.exe`.

### User Data Locations

- App settings and local database: `%LOCALAPPDATA%\IGPostController`
- Thumbnail cache: `%LOCALAPPDATA%\IGPostController\thumb_cache`
- Default download root: `Documents\BIGLOADEB Downloads`

## Build a Release

```powershell
.\build.ps1
```

The release executable is produced under `dist\IGPostController.exe`.

## Versioning

The application version is stored in `ig_post_controller/version.py` as `__version__`.

## Known Limitations

- Public Instagram access can be rate-limited or blocked by Instagram.
- The app does not support stories, likes/comments counts, login/auth, role permissions, auto-update, or upload automation.
- Original-quality downloads are limited to the best public media variant Instagram exposes.
- Use only with public content you are authorized to download.

## Demo Walkthrough

The demo flow selects a registered Instagram account, checks its feed, then reviews the posts saved locally.

1. Run `dist\IGPostController.exe`.
2. Select `Accounts` from the left navigation.
3. Click the `View Posts` button on a registered account row.
4. Select `Downloaded Posts` from the left navigation.
5. Check the saved post card for its thumbnail, caption, `Download Files Again`, and `Delete` actions.

When the app opens, go to the account list. Click the `View Posts` button on the account row to start checking posts for that account.

![Registered account list](docs/demo-screenshots/bigloadeb-use-01-account-list.png)

After the feed check, the same account list shows the latest check time and the available action buttons.

![Feed check result](docs/demo-screenshots/bigloadeb-use-02-feed-result.png)

Open `Downloaded Posts` to review the saved post card, including its thumbnail, caption, and re-download/delete controls.

![Downloaded posts](docs/demo-screenshots/bigloadeb-use-03-downloaded-posts.png)

## Attribution

If you share a fork, demo, article, or derivative, a courteous mention of [@Bum-Boo](https://github.com/Bum-Boo) and the original repository is appreciated. This is a request for credit, not an additional license condition or restriction.

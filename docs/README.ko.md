# BIGLOADEB

> Windows용 계정 중심 Instagram 게시물 수집 및 로컬 미디어 관리 도구.

[English](../README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [中文](README.zh-CN.md)

BIGLOADEB는 여러 business/client account의 public Instagram posts를 수집하고 관리하기 위한 Windows desktop app입니다.

나이가 있거나 기술에 익숙하지 않은 staff도 사용할 수 있도록 account-first workflow, 명확한 feed, 예측 가능한 local folder organization에 초점을 둡니다.

![등록한 계정의 게시물을 모아 보여 주고, 게시물마다 다운로드 버튼이 있는 피드 화면](demo-screenshots/bigloadeb-use-02-feed-result.png)

## Safety / Privacy Boundaries

- public 또는 authorized content만 대상으로 합니다.
- private access를 우회하지 않습니다.
- password나 credential을 저장하지 않습니다.
- media와 metadata는 local storage와 SQLite에 저장됩니다.
- upload/post automation은 구현하지 않습니다.
- public Instagram access는 Instagram 정책, rate limit, session 상태에 따라 제한될 수 있습니다.
- 공개 Instagram 콘텐츠를 허가된 목적과 계정에 한해 사용하세요.

## Features

- public Instagram profile URL 수동 등록.
- username과 display name이 있는 account list screen에서 시작.
- app launch 시 registered account의 new post 확인.
- combined online feed 또는 per-account feed 보기.
- image-only / video-only post filtering.
- date sorting.
- carousel support, caption copy, media preview가 있는 post detail view.
- account-based local folder로 post download.
- downloaded post를 SQLite로 tracking.
- downloaded post를 `posted to cafe`로 표시.
- local storage와 local tracking에서 downloaded post 삭제.
- Settings에서 language와 theme 전환.

## Project Layout

- `ig_post_controller/` - app source code
- `tests/` - regression and smoke tests
- `build.ps1` - Windows release build script
- `build_debug.ps1` - console debug build
- `build_debug_onefile.ps1` - console one-file debug build
- `IGPostController.spec` - PyInstaller spec file

## Development Setup

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

1. 이 repository의 GitHub Releases page를 엽니다.
2. latest Windows release asset을 다운로드합니다.
3. release가 `.zip`으로 packaged되어 있으면 archive를 압축 해제합니다.
4. `IGPostController.exe`를 실행합니다.

### User Data Locations

- App settings and local database: `%LOCALAPPDATA%\IGPostController`
- Thumbnail cache: `%LOCALAPPDATA%\IGPostController\thumb_cache`
- Default download root: `Documents\BIGLOADEB Downloads`

## Build a Release

```powershell
.\build.ps1
```

release executable은 `dist\IGPostController.exe` 아래에 생성됩니다.

## Versioning

application version은 `ig_post_controller/version.py`의 `__version__`에 저장됩니다.

## Known Limitations

- Public Instagram access는 Instagram에 의해 rate-limited 또는 blocked될 수 있습니다.
- stories, likes/comments counts, login/auth, role permissions, auto-update, upload automation은 지원하지 않습니다.
- Original-quality download는 Instagram이 노출하는 best public media variant로 제한됩니다.
- 다운로드 권한이 있는 공개 콘텐츠에만 사용하세요.

## Demo Walkthrough

demo flow는 registered Instagram account를 선택하고 feed를 확인한 뒤 locally saved post를 검토하는 흐름입니다.

1. `dist\IGPostController.exe`를 실행합니다.
2. 왼쪽 navigation에서 `Accounts`를 선택합니다.
3. 등록된 계정 행의 `게시물 보기` 버튼을 클릭합니다.
4. 왼쪽 navigation에서 `Downloaded Posts`를 선택합니다.
5. 저장된 게시물 카드에서 썸네일, 캡션, `파일 다시 받기`, `삭제` 작업을 확인합니다.

앱이 열리면 계정 목록으로 이동합니다. 계정 행의 `게시물 보기` 버튼을 클릭해 해당 계정의 게시물 확인을 시작합니다.

![Registered account list](demo-screenshots/bigloadeb-use-01-account-list.png)

feed check 후 같은 account list에는 latest check time과 available action button이 표시됩니다.

![Feed check result](demo-screenshots/bigloadeb-use-02-feed-result.png)

`Downloaded Posts`를 열면 thumbnail, caption, re-download/delete control이 포함된 saved post card를 확인할 수 있습니다.

![Downloaded posts](demo-screenshots/bigloadeb-use-03-downloaded-posts.png)

## 출처 표기

포크, 데모, 글 또는 파생 작업을 공개할 때 [@Bum-Boo](https://github.com/Bum-Boo)와 원본 저장소를 함께 언급해 주시면 감사하겠습니다. 이는 예의상 부탁드리는 크레딧 요청이며, 추가 라이선스 조건이나 제한이 아닙니다.

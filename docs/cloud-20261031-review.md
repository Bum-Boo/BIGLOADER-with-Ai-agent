# BIGLOADEB 독립 코드 리뷰 (2026-10-31, 클라우드 세션)

- 대상: `main` 기준 커밋 `e079ce5`, 읽기 전용 리뷰. 운영 소스, 매니페스트, 빌드/릴리스 파일은 수정하지 않았습니다.
- 범위: 자격 증명/쿠키 저장, 다운로드 경로 정리(경로 탐색), 부분/실패 다운로드 처리, 재개 가능성, 에이전트용 CLI 노출 범위
- 모든 재현은 합성 데이터만 사용했습니다. 네트워크 호출은 없습니다(`_download_binary`를 mock으로 대체).
- 산출물: 이 문서와 `review/test_cloud_20261031_repro.py`(재현 테스트 5개, **통과 = 결함이 아직 재현됨**)

## 테스트 실행 결과

```bash
python3 -m venv $S/venv
$S/venv/bin/pip install "PySide6>=6.8" "instaloader>=4.14" "requests>=2.32" pytest   # PySide6 6.12.0, instaloader 4.16, requests 2.34.2
apt-get install -y libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3          # Qt 오프스크린 실행용 (Linux VM)
export QT_QPA_PLATFORM=offscreen LOCALAPPDATA=$S/lad HOME=$S/home
$S/venv/bin/python -m pytest -q -p no:cacheprovider tests    # 64 passed, 3 subtests passed
$S/venv/bin/python -m pytest -q -p no:cacheprovider review   # 5 passed (R1~R5 재현)
```

참고: 시스템 라이브러리 `libEGL.so.1`이 없으면 Qt를 import하는 테스트 7개가 수집 단계에서 실패합니다. 코드 결함이 아니라 VM 환경 문제입니다. Windows 전용 동작(후행 점, 예약 이름, ADS)은 Linux에서 직접 실행할 수 없으므로 문자열 단위로만 확인했습니다.

## 요약

| ID | 분류 | 심각도 | 내용 |
|---|---|---|---|
| R1 | 확인된 결함 | P2 | URL이 없는 미디어를 조용히 건너뛰고, 게시물은 "완전히 다운로드됨"으로 기록 |
| R2 | 확인된 결함 | P2 | 기본 폴더명이 같은 날 + 캡션 앞 15자로만 정해져 서로 다른 게시물이 충돌. 일괄 다운로드는 첫 충돌에서 중단 |
| R3 | 확인된 결함 | P3 | 삭제 시 DB 행을 먼저 지우고 파일은 나중에 삭제. 파일 삭제가 실패하면 추적되지 않는 폴더가 남음 |
| R4 | 확인된 결함(Windows 정적) | P3 | `sanitize_for_path`가 후행 `..`, `CON.txt`, 제어 문자, `COM0`을 그대로 둠 |
| R5 | 확인된 결함(Windows 정적) | P3 | 파일 확장자를 URL 경로에서 그대로 가져옴(`:`, 매우 긴 확장자) |
| S1 | 정적 위험 | P3 | 업데이트 SHA256이 선택 사항이고 URL 스킴 제한이 없음 |
| S2 | 정적 위험 | P3 | 다운로드 응답의 Content-Type을 검증하지 않음(HTML 오류 페이지가 `.jpg`로 저장될 수 있음) |
| S3 | 정적 위험 | P3 | `code`가 없는 항목은 shortcode가 `""`가 되어 `ON CONFLICT(shortcode)`에서 서로 다른 게시물이 병합됨 |
| — | 기존 테스트로 확인됨 | — | 임시 폴더 후 rename 교체, 소유하지 않은 폴더 덮어쓰기 거부, 루트 밖 삭제 거부, 재연결 검증, 오래된 임시 폴더 보고 |
| — | 문제 아님 | — | 에이전트용 CLI 없음, 자격 증명/쿠키 저장 없음, 폴더명 경로 탐색 없음 |

---

## R1. 미디어를 조용히 건너뛰고 "완료"로 기록 (P2, 확인된 결함)

- 위치: `ig_post_controller/services/download_service.py:380-383`(`if not source: continue`), `:398-407`, `:499`. 원인이 되는 입력은 `ig_post_controller/services/instagram_service.py:400-421`, `:430-432`, `:442-445`에서 만들어집니다. `media_type == 2`인데 `video_versions`가 없으면 `remote_url=None`이 됩니다.
- 트리거: 캐러셀 3장 중 1장의 `remote_url`이 None인 경우(동영상 URL 누락, 응답 형식 변경 등)
- 관찰: `01.jpg`, `03.jpg`만 저장되는데 `is_downloaded=True`가 되고 `media_json`에는 2개만 기록됩니다. 목록의 `download_folder_missing=False`라서 UI에서는 정상으로 보입니다. 사용자는 누락 사실을 알 수 없습니다(`test_r1_*`).
- 기대: 하나라도 누락되면 실패로 처리하거나, 최소한 "부분 다운로드" 상태를 기록하고 표시해야 합니다.
- 수정 방향: 건너뛴 항목을 세고, 0개보다 많으면 예외를 던져 임시 폴더를 정리합니다(기존 except 경로 재사용). 또는 `meta.json`에 `missing_media` 필드를 남기고 UI에 표시합니다.

## R2. 기본 폴더명 충돌과 일괄 다운로드 중단 (P2, 확인된 결함)

- 위치: `ig_post_controller/utils/text.py:69-72`(날짜 + 캡션 앞 15자), `download_service.py:373`(organized 레이아웃은 shortcode를 포함하지 않음), `:378-430`(모든 미디어를 받은 **뒤에** `:434-436`에서 충돌 확인), `ig_post_controller/ui/main_window.py:779-787`(일괄 루프에 항목별 예외 처리 없음), `:614`
- 트리거: 같은 계정, 같은 날짜에 캡션 앞부분이 같거나(정기 공지, 해시태그로 시작) 캡션이 빈 게시물 두 개. 기본 레이아웃은 `organized`입니다.
- 관찰: 두 번째 게시물은 미디어를 모두 내려받은 뒤 `FileExistsError`로 실패합니다(`test_r2_*`에서 다운로드 1회 후 예외 발생 확인). 시작 시 새 게시물 일괄 다운로드에서는 첫 충돌 이후 게시물이 전부 처리되지 않습니다. 이미 받은 게시물은 유지되지만 이어서 진행(resume)하는 경로가 없습니다. 사용자는 "폴더 이름 충돌" 안내를 받고 직접 제목을 바꿔야 합니다.
- 기대: 서로 다른 게시물은 기본값으로도 충돌하지 않아야 합니다. 일괄 작업은 실패한 항목만 보고하고 나머지를 계속 처리해야 합니다.
- 수정 방향: 충돌하면 `_{shortcode}` 또는 `_2` 접미사로 자동 구분합니다(flat 레이아웃은 이미 shortcode를 포함). 충돌 검사를 다운로드 전에 수행합니다. `main_window.py:779`의 루프를 항목별 try/except로 감싸고 성공/실패 개수를 보고합니다.
- 비고: 다른 shortcode 소유 폴더를 덮어쓰지 않는 **안전성** 자체는 `tests/test_download_service_safety.py:178`에서 이미 확인됩니다. 이 항목의 문제는 충돌이 쉽게 발생한다는 점과 일괄 작업이 중단된다는 점입니다.

## R3. 삭제 순서로 인한 고아 폴더 (P3, 확인된 결함)

- 위치: `download_service.py:338-344`(DELETE를 커밋한 뒤 `_remove_owned_path`)
- 트리거: Windows에서 탐색기나 미디어 플레이어가 파일을 잡고 있어 `shutil.rmtree`가 `PermissionError`로 실패하는 경우
- 관찰: 기록은 사라지고 폴더는 일부 또는 전부 남습니다. UI에는 오류만 표시되고, 남은 폴더를 다시 찾을 경로는 수동 재연결뿐입니다(`test_r3_*`).
- 기대: 파일 삭제가 실패하면 기록을 유지하거나, 최소한 "파일 삭제 실패" 상태로 남겨야 합니다.
- 수정 방향: 먼저 폴더를 `.backup-` 이름으로 rename(원자적)하고, DB 삭제에 성공한 뒤 rmtree를 실행합니다. rename이 실패하면 DB를 건드리지 않습니다.

## R4. Windows에서 안전하지 않은 이름이 남음 (P3, Windows 정적 확인)

- 위치: `ig_post_controller/utils/text.py:6`, `:51-61`
- 관찰(`test_r4_*`):
  - 길이를 자르면 `..`로 끝납니다. Windows는 후행 점을 조용히 제거하므로 DB의 `folder_path`와 실제 이름이 달라집니다. Win32 API 경로에서는 정규화 덕분에 대부분 동작하지만, 다른 도구로 복사하거나 비교할 때 불일치가 생깁니다. 캡션 snippet의 `..`는 `strip(" .")`로 제거되지만, 최대 길이를 자를 때 다시 추가됩니다.
  - `CON.txt`, `COM0`처럼 확장자가 붙은 예약 장치 이름이나 목록에 없는 예약 이름이 통과합니다. 회사명이나 사용자명 폴더(`download_service.py:369-372`), `title_override`에서 발생할 수 있습니다.
  - `\x01` 같은 제어 문자가 남아 Windows에서 `mkdir`이 실패합니다.
- 수정 방향: `[\x00-\x1f]`를 제거하고, 접미사를 `..` 대신 `_`나 `…`로 바꿉니다. 예약 이름은 `value.split(".")[0].upper()` 기준으로 검사하고 `COM0`/`LPT0`, `CONIN$`/`CONOUT$`를 추가합니다.

## R5. URL에서 그대로 가져온 확장자 (P3, Windows 정적 확인)

- 위치: `download_service.py:638-646`, `:385`
- 관찰: `https://cdn.invalid/x.jpg:ads`는 `.jpg:ads`가 되어 NTFS에서는 `01.jpg`의 대체 데이터 스트림에 기록됩니다. 확장자가 300자 이상이면 그대로 쓰여 경로 길이 초과가 날 수 있습니다(`test_r5_*`). 디렉터리 구분자는 포함될 수 없으므로 경로 탐색은 아닙니다. URL 출처가 Instagram 응답이라서 현실적인 위험은 낮습니다.
- 수정 방향: `{.jpg,.jpeg,.png,.webp,.heic,.mp4,.mov}` 허용 목록에 없으면 미디어 유형별 기본값을 사용합니다.

## 정적 위험

- **S1 업데이트 무결성** (`ig_post_controller/services/update_service.py:84`, `:98-107`, `:115-121`, `main_window.py:903`, `:911`): 매니페스트에 `sha256`이 없으면 검증 없이 설치 프로그램을 실행합니다. `installer_url` 스킴을 제한하지 않아 `http://`, `file://`도 허용됩니다. `size`는 읽기만 하고 검사하지 않으며 전체 내용을 메모리로 읽습니다. 매니페스트와 해시가 같은 릴리스에서 오므로 인증이 아니라 무결성만 보장합니다. 수정 방향: `sha256` 필수화, `https://github.com/Bum-Boo/…` 접두사 허용 목록, `size` 검사. 가능하면 코드 서명도 검토합니다.
- **S2 응답 형식 미검증** (`download_service.py:629-635`): 200 응답이면 HTML 로그인/차단 페이지도 미디어 파일로 저장됩니다. 수정 방향: `Content-Type`이 `image/`나 `video/`로 시작하는지, `Content-Length`와 실제 크기가 같은지 확인합니다. urllib3 2.x는 길이 불일치를 기본적으로 오류로 처리하지만 1.x는 그렇지 않습니다.
- **S3 빈 shortcode** (`instagram_service.py:381`): `code`가 없는 응답 항목은 `shortcode=""`로 저장되어, 서로 다른 게시물이 `ON CONFLICT(shortcode)`로 하나의 행에 덮어쓰입니다. 수정 방향: shortcode가 빈 항목은 건너뜁니다.

## 문제 아님 / 이미 다뤄짐

- **에이전트용 CLI**: 저장소에는 CLI가 없습니다. 진입점 `ig_post_controller/main.py`는 PySide6 GUI만 띄우고 `sys.argv`는 로그에 남기기만 합니다(`main.py:108`). 따라서 에이전트에 노출되는 명령 표면은 없습니다. 저장소 이름의 "AI agent"는 코드에 반영되어 있지 않습니다.
- **자격 증명/쿠키**: 로그인, 세션 저장/로드(`login`, `load_session`, `save_session`) 호출이 없습니다. instaloader는 익명으로만 사용하고 `requests.Session` 쿠키는 메모리에만 있습니다. DB 테이블(settings/accounts/posts/downloads)에도 비밀 값이 없습니다. README의 "비밀번호를 저장하지 않음"과 일치합니다.
- **폴더명 경로 탐색**: `/`, `\`, `:`를 제거하고 앞뒤 `.`도 제거하므로 `..`는 `untitled`가 됩니다. 사용자명, 회사명, 제목 모두 같은 함수를 거칩니다.
- **부분 실패 안전성**: 임시 폴더에 받은 뒤 rename으로 교체하고, 실패하면 백업을 복원합니다. DB 실패 시에도 롤백합니다. 이 동작은 `tests/test_download_service_safety.py:85`, `:148`, `:178`, `:199`, `:112`, `:131`, `:405`와 `tests/test_storage_recovery.py`에서 이미 검증됩니다.
- **재개 가능성**: 게시물 단위로 원자적입니다(부분 폴더가 최종 위치에 남지 않음). 다만 파일 단위 이어받기는 없고, 일괄 작업 재개 문제는 R2에 포함했습니다.

## 재현 테스트 실행

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q review/test_cloud_20261031_repro.py
```

각 테스트는 현재(결함) 동작을 assert합니다. 수정한 뒤에는 해당 테스트가 실패하므로 assert를 기대 동작으로 바꿔 회귀 테스트로 쓰면 됩니다.

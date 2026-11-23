# V-Concept Studio — Claude Code 인수인계 문서

이 파일은 Claude Code 가 이 저장소에서 작업을 시작할 때 자동으로 읽습니다. 이전 작업(claude.ai 대화, 2026-11-11 ~ 11-16)에서
결정된 것, 지켜야 할 규칙, 함정을 정리했습니다. **작업 전에 끝까지 읽으세요.**

현재 버전: **PC v0.6.0 / 웹 v0.4.0** (2026-11-25 릴리스) · 저장소: `grafficcioso/V-Concept-Studio` (공개)

---

## 0. 사용자와 일하는 규칙 (가장 중요)

1. **존댓말로만** 대답합니다. 사용자는 한국어로 이야기하고, 개발 용어보다 쉬운 말을 선호합니다.
2. **기능 업데이트는 바로 하지 않습니다.** 요청받은 기능은 아래 [7. 대기 목록](#7-대기-목록)에 모아 두고,
   목록을 보여 주며 **"업데이트할까요?"** 라고 물은 뒤, 사용자가 하라고 하면 한꺼번에 작업·릴리스합니다
   (릴리스 한 번에 시간이 많이 들기 때문). 질문·설명·조사·버그 원인 분석은 바로 해도 됩니다.
   단, 사용자가 "지금 진행해"라고 명시하면 그 항목은 바로 합니다.
3. **추가 비용이 드는 방법은 피하고, 비용 위험은 미리 말합니다.** (Claude 구독 외 지출 원치 않음.)
   테스트·검증에서 **실제 OpenAI API 를 호출하지 마세요** — 데모 모드나 가짜(mock) 응답으로 확인합니다.
4. **비밀 정보 규칙**
   - OpenAI API 키는 Windows 자격 증명 관리자(keyring)에만. settings.json·SQLite·로그·이미지 메타데이터·응답에 절대 넣지 않습니다.
   - 구글 **데스크톱** 클라이언트 보안 비밀번호와 refresh token 도 키 저장소에만 (`crs/sync/account.py`).
   - 사용자에게 비밀번호·토큰·보안 비밀번호를 채팅으로 보내 달라고 하지 마세요. 사용자가 프로그램 설정에 직접 넣습니다.
   - 공개 저장소이므로 비밀 정보·사용자 덤프(.dmp)·로그를 커밋하지 마세요.
   - 웹 클라이언트 ID(`web/config.json`)는 공개 값이라 커밋되어 있어도 괜찮습니다.
5. 끝났을 때는 **무엇을 확인했고 무엇을 확인하지 못했는지** 분명히 말합니다 (실제 Windows, 실제 폰, 실제 구글 계정 등).

---

## 1. 무엇을 하는 프로그램인가

캐릭터 원화 1장을 OpenAI 이미지 API(`images.edit`)로 3D 모델링용 참고 이미지로 바꾸는 Windows 10/11 프로그램 + 폰 웹앱.

- 생성 기능(`crs/functions.py` 의 `FUNCTIONS`): 정면화 `front_view`, 파트 분할 `parts_separation`, 사면도 `four_views`,
  홍채 복원 `iris_restore`, 업스케일 `upscale`, 부분 수정 `retouch`(빨간 펜 영역 = markup), 파트 사면도 `part_views_*`,
  AI 조합 `ai_compose`, 화풍 추출 `style_extract`(텍스트 모델, Responses API + json_schema)
- 라이브러리/에셋, 조합 화면, 사용량·비용 기록, 월 예산, 충전액 표시, 자동 업데이트
- **구글 드라이브 동기화**(v0.4): PC ↔ 폰 웹앱이 생성 결과·에셋 공유
- **Claude Code 연결(MCP 서버)**(v0.5): `VConceptStudio-mcp.exe`
- **폰 → PC 원본 보내기**(v0.5): 웹앱에서 보낸 원본이 PC '받은 원본'으로

모델·가격은 코드에 하드코딩하지 않고 `defaults/models.json`(사용자 편집 가능)에서 읽습니다.

---

## 2. 저장소 구조

```
main.py                 PC 앱 진입점 (--mcp 를 주면 MCP 서버로 실행)
mcp_main.py             MCP 서버 전용 진입점 (콘솔 EXE 로 빌드됨)
crs/                    파이썬 패키지 (이름은 옛 이름 crs 그대로 — 바꾸지 마세요)
  __init__.py           APP_NAME, APP_VERSION, EXE_NAME, GITHUB_REPO  ← 버전은 여기 하나
  config.py             Settings(DEFAULTS), AppPaths, 기본 프롬프트 복사·자동 갱신
  db.py                 SQLite 스키마/마이그레이션(_migrate), 동기화 열, tombstones, usage_log.source
  workspace.py          UI 와 무관한 핵심 객체 (프로젝트·이미지·에셋·프롬프트·서비스)
  jobs.py               생성 1건 공통 처리: GenJob, validate, run, record_success/failure (창·MCP 공용)
  functions.py          기능 목록, split_prompt/join_prompt/compose_prompt (Positive/Negative)
  pricing.py            Catalog (models.json), 비용 추정
  keystore.py           API 키·비밀값 (keyring, 실패 시 이번 실행 메모리만)
  services/             image_service(OpenAI 이미지, 데모), style_service(화풍 추출)
  sync/                 drive.py(구글 OAuth PKCE 루프백 + Drive REST), engine.py(동기화 규칙),
                        account.py(인증 정보), lock.py(프로세스 간 동기화 잠금, QLockFile)
  mcp_server.py         MCP stdio 서버 (JSON-RPC 직접 구현, 외부 mcp 패키지 불필요)
  updater.py            GitHub 릴리스 확인 → zip 받기 → sha256 → PowerShell 스크립트로 교체·재실행
  watchdog.py, hard_exit.py, app.py   멈춤 진단(hang.log + minidump), Windows 종료 처리
  ui/                   PySide6 화면들 (main_window, generate_tab, settings_dialog, sync_ui, mcp_ui, …)
defaults/               prompts/*.md(기본 프롬프트), models.json, CHANGELOG.md, prompt_history.json
web/                    폰 웹앱 (순수 ES 모듈, IndexedDB, PWA) → GitHub Pages
tests/                  PC 테스트 (pytest, offscreen Qt, 가짜 API·가짜 드라이브)
tests_web/              웹앱 테스트 (Playwright + 시스템 Chrome, 폰 화면 크기)
tools/                  빌드·릴리스·진단 스크립트
crs.spec                PyInstaller onedir: VConceptStudio.exe(창) + VConceptStudio-mcp.exe(콘솔)
```

사용자 PC 의 위치:
- 데이터: `문서\V-Concept Studio\` (projects, library, prompts, config\models.json, data\*.sqlite, logs)
- 설정: `%APPDATA%\V-Concept Studio\settings.json`  (테스트는 `CRS_CONFIG_DIR` 로 바꿈)
- 옛 이름 `CharacterReferenceStudio` 에서 자동 이전 코드가 있음 (`LEGACY_APP_NAME`)

---

## 3. 개발·테스트

```bash
pip install -r requirements-dev.txt          # Windows: python -m pip ...
python main.py                               # PC 앱 실행 (run_dev.bat)
QT_QPA_PLATFORM=offscreen python -m pytest tests -q        # PC 테스트 (현재 79개)
python tools/build_web.py _site && python -m pytest tests_web -q   # 웹 테스트 (현재 9개, Chrome 필요)
# uv 로 돌릴 때: uv run --with-requirements requirements-dev.txt --with playwright python -m pytest ...
#   (pytest-playwright 플러그인은 넣지 말 것 — 자체 page 픽스처·이벤트 루프와 충돌)
```

- 테스트는 **네트워크·실제 API·실제 키 저장소를 쓰지 않습니다.** `tests/conftest.py` 의 autouse 픽스처가
  `keystore._secure_backend` 를 막습니다. (예전에 테스트가 Windows 자격 증명 관리자에 가짜 키를 남겨
  다른 테스트가 확인 창에서 멈춘 적이 있음 — 이 픽스처를 지우지 마세요.)
- OpenAI 는 `make_mock_client(handler)` (httpx MockTransport)로, 드라이브는 `tests/fake_drive.py`(로컬 HTTP 서버)로 흉내냅니다.
- `tests/test_mcp.py` 는 공식 `mcp` SDK 클라이언트로 실제 서버 프로세스를 띄워 확인합니다 (SDK 2.x 는 속성 이름이 snake_case).
- 새 기능에는 테스트를 같이 추가하세요. UI 는 offscreen 으로 스크린샷(`widget.grab().save(...)`)을 찍어 눈으로 확인하면 좋습니다.

---

## 4. 릴리스 (태그를 직접 만들지 않습니다)

1. `crs/__init__.py` 의 `APP_VERSION` 을 올리고, `defaults/CHANGELOG.md` 맨 위에 사용자용 한국어 변경 내역을 씁니다
   (업데이트 후 "무엇이 바뀌었나" 창에 그대로 보입니다). 웹을 바꿨다면 `web/js/app.js` 의 `WEB_VERSION` 과 `web/WEB_CHANGELOG.md` 도.
2. `python tools/check_version.py v0.x.y` 로 확인 → main 에 push.
3. GitHub Actions **Build & Release**(windows-latest): 테스트 → PyInstaller → EXE 실행 확인 → MCP EXE 확인(`tools/smoke_mcp_exe.py`)
   → 업데이트 후 창 닫힘 확인(`diag_exe.ps1`) → 업데이트 스크립트 실제 교체 확인(`diag_update_script.py`)
   → zip + sha256 → **해당 버전 릴리스가 없을 때만** `gh release create`. 사용자 프로그램은 시작할 때 이 릴리스를 감지해 업데이트를 묻습니다.
4. **Deploy web app**: `web/**` 등이 바뀌면 웹 테스트 후 GitHub Pages(https://grafficcioso.github.io/V-Concept-Studio/)에 배포.
5. CI 확인: `gh run list -R grafficcioso/V-Concept-Studio`, 실패 로그 `gh run view <id> --log-failed`.
   테스트 단계는 `--timeout=150` 이 걸려 있어서 멈추면 스택이 로그에 찍힙니다.

버전을 올리지 않은 push 는 테스트·빌드만 하고 릴리스는 만들지 않습니다.

---

## 5. 핵심 설계와 꼭 알아야 할 함정

### 생성
- 생성은 `crs/jobs.py` 하나로 처리합니다 (창의 `GenerationController`, MCP 모두). 실패 시 **자동 재시도 안 함**(과금 위험).
- 투명 배경 입력은 API 가 검게 읽어 결과에 검은/회색 테두리가 생겼었음 → `prepare_input_image` 가 **흰 배경으로 합성**해서 보냄.
- 프롬프트 저장 형식: 앞부분 = Positive, `### Negative` 줄 아래 = Negative. 보낼 때 `compose_prompt` 가
  Positive → 화풍 → "Avoid (negative prompt)…" 문단 순서로 붙임 (이미지 API 에 negative 칸이 없음). 웹(`web/js/prompt.js`)도 같은 규칙.
- 사용자가 고치지 않은 기본 프롬프트는 `defaults/prompt_history.json`(예전 기본값 해시)로 판별해 자동 교체합니다.
  **기본 프롬프트를 바꿀 때는 바꾸기 전 내용의 해시를 이 파일에 추가하세요.**

### 화면 멈춤 (v0.3.4 에서 해결, 다시 깨지 않게)
- 원인: 투명 PNG 를 `QGraphicsPixmapItem` 기본 MaskShape + `fitInView(item)` 으로 맞출 때, Windows 11 스타일에서 스크롤바
  재계산이 반복되며 수십 초~수십 분 멈춤. 해결: `crs/ui/image_view.py` — `BoundingRectShape`, 스크롤바 끔,
  `fitInView(sceneBoundingRect())`, 뷰포트 크기가 실제로 바뀔 때만 다시 맞춤. **이 부분을 되돌리지 마세요.**
- 멈춤 진단 도구: `crs/watchdog.py` 가 8초 이상 멈추면 `logs/hang.log` + minidump 를 남김.

### Windows 특이사항
- 종료 시 접근 위반 → `crs/hard_exit.py` 가 `OleFlushClipboard` 후 `TerminateProcess`(ctypes argtypes 필수) 사용.
- 업데이트 스크립트 실행은 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (DETACHED_PROCESS 를 쓰면 PowerShell 이 안 뜸).
  스크립트는 UTF-8 BOM, robocopy, 한글 경로 지원. 업데이트 전에 같은 폴더의 `VConceptStudio-mcp.exe` 를 종료함.
- 설정 파일이 백신·동기화 프로그램에 잠깐 잡히는 경우 → `Settings.save()` 가 재시도.
- 프로그램은 한 개만 실행 (QLockFile). MCP 서버는 창과 **동시에** 돌 수 있음 → SQLite `timeout=30`, 동기화는 `sync/lock.py` 잠금,
  창은 4초마다 `PRAGMA data_version` 을 보고 다른 프로세스가 바꾼 내용을 다시 읽음.

### 구글 드라이브 동기화 (`crs/sync`, `web/js/sync.js` — 두 쪽 규칙이 같아야 함)
- 권한은 `drive.file`(앱이 만든 파일만). 폴더 1개(appProperties `vc_root=1`), 항목 = PNG + description(JSON 메타) + appProperties(`vc_item=1`, `vc_uid`).
- 메타 `kind`: `output` / `asset`(양방향 동기화), `inbox`(폰 → PC 한 방향: PC 가 받으면 드라이브 사본을 휴지통으로, 웹 기록은 유지).
- 이름·태그·즐겨찾기·분류는 **나중에 고친 쪽이 이김** — 시간은 반드시 **UTC, …Z, 마이크로초**(`db.now_precise`, 비교는 `ts_key`).
- 지운 항목은 tombstone → 드라이브 휴지통 → 다른 기기에서도 삭제. 구글 계정(폴더)이 바뀌면 연결 정보만 초기화(파일은 안 지움).
- 원본(입력)은 동기화하지 않음. inbox 만 예외.
- 구글 클라우드 설정: 앱은 **프로덕션 게시 완료**, 브랜딩에 개인정보처리방침(`web/privacy.html`) 연결,
  웹 클라이언트 ID 는 `web/config.json`, 데스크톱 클라이언트 ID·보안 비밀번호는 사용자가 PC 설정에 직접 입력.

### MCP 서버 (`crs/mcp_server.py`)
- stdio JSON-RPC 를 직접 구현 (한 줄 = 메시지). **stdout 에는 프로토콜 외에 아무것도 쓰면 안 됨** (main 에서 sys.stdout 을 stderr 로 돌림).
- 도구 호출은 **스레드 하나**(ThreadPoolExecutor 1개)에서만 처리 → SQLite 연결을 한 스레드에서만 씀. 생성 작업은 별도 워커 스레드 + 별도 DB 연결.
- 도구가 MCP 콘텐츠(텍스트/이미지 블록)를 직접 돌려줄 때는 `Content([...])` 로 감쌈. 그냥 list 를 돌려주면 JSON 텍스트로 보냄
  (예전에 list 결과가 콘텐츠로 오인되어 공식 클라이언트가 거부한 버그가 있었음).
- 설정 `mcp_enabled`(기본 꺼짐), `mcp_max_images_per_call`(6), `mcp_daily_budget_usd`(2.0) — 넘으면 거절. 월 예산도 지킴.
  사용량은 `usage_log.source='mcp'` 로 구분. 생성 단계(steps)는 앞 결과를 다음 입력으로 이어서 실행.
- 사용자 사용법: 설정 → Claude 연결 → 허용 → 등록 명령 복사(`claude mcp add --scope user v-concept -- "...VConceptStudio-mcp.exe"`),
  밖에서는 PC 에서 `claude remote-control` + 폰 Claude 앱 Code 탭.

### 파트 사면도 · 헤드+헤어 세트 (v0.6.0)
- 입력 순서: 1) 잘라낸 파트 2) (헤드 사면도) 3) (원본 원화). `build_part_views_prompt` / 웹 `partPrompt` 가 "SECOND/THIRD image" 안내를 이 순서에 맞춘다.
  사용자가 `{reference_instruction}` 을 지운 프롬프트에도 안내는 Positive 끝에 붙인다. **기본 프롬프트 파일은 바꾸지 않았다.**
- 세트: `PartViewsDialog` 가 헤드/헤어 레이어를 따로 가짐(다른 쪽은 `ghost` 파랑). 헤드 요청이 끝나면 `_continue_set_later`
  (컨트롤러 워커 정리를 기다림) → 헤어 요청을 `ctl.start(..., ask=False)` 로 보냄 (예산 초과일 때만 확인 창). 헤드 실패 시 헤어 파트 입력 삭제.
- 결과 params 에 `head_sheet_id`, `set_step` → [헤드와 겹쳐 보기](`OverlayDialog`, 웹 `#overlay`).
- 웹은 잘라낸 파트를 IndexedDB `kind:"part_input"` 으로 저장 (기록·동기화에 안 나옴).

### 핀터레스트 (v0.6.0, `crs/pinterest.py`, `crs/ui/pinterest_ui.py`)
- 공개 보드 RSS(`/<user>/<board>.rss`, 사용자만 있으면 `feed.rss`) — 최근 약 25개, 로그인·키·비용 없음. 비공식이라 언제든 막힐 수 있음.
  2026-11-24 확인: RSS 200 응답, `/originals/` 원본 크기 받기 됨(실패 시 `/736x/`).
- pubDate 는 핀 날짜로 보임 — '보드에 추가한 날짜'인지는 **확인 못 함**.
- 가져온 핀: 원본 + 태그 `외부 출처, pinterest` + 메모(출처·공부용·판매/커미션/게시 금지). `pinterest_pins` 표로 중복 방지.
- 창: 네트워크는 QThread. 가져온 뒤 단계 생성은 생성 탭 `run_batch`(시작 전 한 번 확인, 실패 시 멈춤, 자동 재시도 없음).
- MCP: `pinterest_list`, `pinterest_import`(confirm 없으면 미리보기만 → 사용자 OK 후 confirm=true, 생성은 tool_generate 의 장수·하루 상한 그대로).
  테스트는 `Server.pinterest_get` 에 가짜 네트워크.
- 디렉터 결정(2026-11-25): 핀은 정면화·사면도 변환까지 허용, 단 '외부 출처' = 공부·참고용.

### 웹앱 (`web/`)
- 빌드 도구 없음(순수 ES 모듈). IndexedDB `vconcept` v2 (images, usage, tombstones). 서비스 워커 캐시 이름은 빌드 때 바뀜.
- OpenAI 는 브라우저에서 직접 호출(CORS 허용 확인됨). API 키는 그 기기의 localStorage 에만.
- 구글 로그인은 Google Identity Services 토큰 방식(약 1시간마다 재연결 필요할 수 있음). 테스트에서는 `window.google` 을 가짜로 넣고
  `window.__VC_DRIVE_BASE` 로 가짜 드라이브를 가리킴.
- 사용자는 **아이폰** 사용 (iOS 는 웹앱 공유 대상(Share Target) 불가).

---

## 6. 확인하지 못한 것 (사용자에게 확인 부탁할 것)

- 실제 구글 계정으로 PC·폰 동기화, 폰 → PC 원본 보내기 (가짜 드라이브로만 확인)
- 사용자 PC 의 실제 Claude Code 에서 MCP 연결·원격 제어로 지시 (CI 에서 공식 MCP 클라이언트 + 실제 EXE, 데모 모드로는 확인)
- 실제 폰(아이폰 Safari)에서 웹앱 전체 흐름, 웹에서 실제 OpenAI 호출
- 사용자 확인 완료: v0.3.4 멈춤 해결("드디어 되네"), 부분 수정 배경 문제 수정

---

## 7. 대기 목록

사용자가 "해"라고 하면 한꺼번에 작업합니다. 새 요청은 여기 추가하고 사용자에게 목록을 보여 주세요.
(VStudio 쪽 대기 목록: `../Handoff/update_waitlist.md` — 둘 다 맞춰 둘 것)

- (비어 있음 — v0.6.0 에서 폰 파트 사면도·헤드/헤어 크기 맞추기·핀터레스트 가져오기 완료)
- 참고: 핀터레스트 '주기적 자동 감시'는 만들지 않았다 (지금은 [핀 불러오기]·MCP 로 사람이 시킬 때만). 필요하면 대기 목록에 추가.

보류(사용자가 "다음에"라고 함):
- **Winamp 스킨 같은 UI** (보라색 고양이 모양 Y2K 기기 풍). 사용자가 스킨 이미지를 준비하면 시작.

---

## 8. 주요 결정 기록 (요약)

- 공개 저장소 + GitHub Releases 로 자동 업데이트 (사용자 GitHub: grafficcioso)
- 프로그램 이름 Character Reference Studio → V-Concept Studio (v0.2), 데이터 폴더도 자동 이전
- 모바일은 앱 대신 웹앱(PWA) + GitHub Pages, 라이브러리 공유는 구글 드라이브(생성 결과 + 에셋, 원본 제외)
- 프롬프트 Positive/Negative 분리 (v0.4.1)
- Claude Code 연결은 MCP 서버 별도 콘솔 EXE, 기본 꺼짐 + 비용 상한 (v0.5.0)

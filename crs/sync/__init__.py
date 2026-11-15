"""구글 드라이브 동기화 (PC 프로그램 ↔ 웹앱).

공통 형식 (웹앱 web/js/sync.js 와 같아야 한다):
- 드라이브에 앱이 만든 폴더 1개 (appProperties vc_root=1). 권한은 drive.file (앱이 만든 파일만 보임).
- 항목 = PNG 파일 1개. appProperties: vc_item=1, vc_uid=<uuid>.
  description = JSON 메타데이터 {v, uid, kind('output'|'asset'), function, category, name, tags, note,
  favorite, created, updated, model, quality, size, background, project, request_id}
- 규칙: 새 항목은 올리고/받는다. 이름·태그·즐겨찾기·분류는 updated 가 늦은 쪽이 이긴다.
  한쪽에서 지우면 드라이브 파일을 휴지통으로 옮기고(30일 복구 가능), 다른 쪽도 다음 동기화 때 지운다.
"""

META_VERSION = 1
FOLDER_NAME = "V-Concept Studio 동기화"
SCOPE = "https://www.googleapis.com/auth/drive.file"

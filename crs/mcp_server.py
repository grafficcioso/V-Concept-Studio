"""MCP 서버 모드: Claude Code 가 V-Concept Studio 기능을 도구로 부를 수 있게 한다.

실행: VConceptStudio-mcp.exe  (또는  python main.py --mcp)
Claude Code 등록 예:  claude mcp add v-concept -- "C:\\...\\VConceptStudio-mcp.exe"

- 표준 입출력(stdio) 위의 JSON-RPC 2.0 (MCP stdio 전송: 한 줄 = 메시지 하나).
- 프로그램 설정의 [Claude 연결]에서 켜야 동작한다(기본 꺼짐).
- 유료 생성은 '1번에 최대 장수'와 'MCP 하루 추정 사용액 상한'을 넘으면 거절한다.
- API 키는 지금처럼 Windows 자격 증명 관리자에서만 읽고, 어떤 응답에도 넣지 않는다.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import logging
import shutil
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import APP_NAME, APP_VERSION

log = logging.getLogger("crs.mcp")

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
PIPELINE_FUNCTIONS = ("front_view", "parts_separation", "four_views", "iris_restore", "upscale")
FALLBACK_COST_PER_IMAGE = 0.30     # 요금표로 추정할 수 없을 때 상한 계산에 쓰는 보수적인 값

INSTRUCTIONS = (
    "V-Concept Studio: 캐릭터 원화로 정면화(front_view), 파트 분할(parts_separation), 사면도(four_views), "
    "홍채 복원(iris_restore), 업스케일(upscale) 이미지를 OpenAI 로 생성하고 라이브러리를 관리하는 PC 프로그램입니다.\n"
    "- 폰에서 보낸 원본은 list_inbox 로 확인합니다(메모에 원하는 작업이 적혀 있을 수 있음). 처리하면 자동으로 '처리됨'이 됩니다.\n"
    "- generate 의 steps 는 순서대로 이어서 실행됩니다. 예: ['front_view','four_views'] = 정면화 결과로 사면도.\n"
    "- 생성은 유료입니다. 사용자가 시킨 만큼만 만들고, 같은 요청을 반복하지 마세요. 거절되면 사용자에게 이유를 전하세요.\n"
    "- 한 장에 30초~2분 걸립니다. wait_sec 안에 안 끝나면 job_status 로 확인하세요.\n"
    "- 결과 파일 이름을 정리할 때는 원본을 옮기지 말고 export_images 로 원하는 폴더에 원하는 이름으로 복사하세요.\n"
    "- 핀터레스트: pinterest_list 로 공개 보드의 최근 핀을 보고, pinterest_import 는 먼저 confirm 없이 불러 "
    "장수·예상 비용을 사용자에게 보여 준 뒤 OK 를 받으면 confirm=true 로 다시 부르세요. 가져온 이미지는 '외부 출처' — "
    "공부·참고용이며 판매·커미션·게시용으로 쓰지 않습니다."
)


def _schema(props: dict, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOLS = [
    {"name": "status", "description": "연결 상태, 생성 한도(1번 최대 장수·하루 상한·오늘 사용액), 받은 원본 수, 진행 중 작업을 봅니다.",
     "inputSchema": _schema({})},
    {"name": "list_projects", "description": "프로젝트(캐릭터) 목록과 원본·결과 개수.", "inputSchema": _schema({})},
    {"name": "list_inbox", "description": "폰 웹앱에서 [PC로 보내기] 한 원본 중 아직 처리하지 않은 것. 메모(note)에 원하는 작업이 있을 수 있습니다.",
     "inputSchema": _schema({"include_done": {"type": "boolean", "description": "처리한 것도 포함"},
                             "previews": {"type": "boolean", "description": "작은 미리보기 이미지 포함 (기본 false)"}})},
    {"name": "mark_inbox_done", "description": "받은 원본을 '처리됨'으로 표시 (generate 로 처리하면 자동).",
     "inputSchema": _schema({"image_ids": {"type": "array", "items": {"type": "integer"}}}, ["image_ids"])},
    {"name": "list_images", "description": "이미지 목록 (원본·결과). 파일 경로 포함.",
     "inputSchema": _schema({"project": {"type": "string", "description": "프로젝트 이름 (없으면 전체)"},
                             "kind": {"type": "string", "enum": ["original", "output"]},
                             "function": {"type": "string", "description": "결과의 기능 키 (예: four_views)"},
                             "query": {"type": "string", "description": "이름·태그·메모 검색"},
                             "limit": {"type": "integer", "minimum": 1, "maximum": 200}})},
    {"name": "show_image", "description": "이미지 하나를 직접 봅니다 (최대 768px 미리보기).",
     "inputSchema": _schema({"image_id": {"type": "integer"}}, ["image_id"])},
    {"name": "import_images", "description": "이 PC 의 이미지 파일(PNG/JPG/WEBP)을 원본으로 프로젝트에 가져옵니다 (원본 파일은 그대로).",
     "inputSchema": _schema({"paths": {"type": "array", "items": {"type": "string"}},
                             "project": {"type": "string", "description": "프로젝트 이름 (없으면 새로 만듦)"}},
                            ["paths", "project"])},
    {"name": "generate", "description": (
        "유료 이미지 생성. 각 이미지에 steps 를 순서대로 적용합니다 (앞 단계 결과가 다음 입력). "
        "총 장수 = 이미지 수 × 단계 수. 기능: front_view(정면화), parts_separation(파트 분할), four_views(사면도), "
        "iris_restore(홍채 복원), upscale(업스케일). 프롬프트는 프로그램에 저장된 기본 프롬프트(Positive/Negative)를 씁니다."),
     "inputSchema": _schema({
         "image_ids": {"type": "array", "items": {"type": "integer"}, "description": "입력 이미지 id (원본 또는 결과)"},
         "steps": {"type": "array", "items": {"type": "string", "enum": list(PIPELINE_FUNCTIONS)}},
         "project": {"type": "string", "description": "결과를 넣을 프로젝트 이름 (없으면 입력 이미지의 프로젝트)"},
         "model": {"type": "string"}, "quality": {"type": "string", "description": "low / medium / high / auto"},
         "size": {"type": "string"}, "background": {"type": "string"},
         "style": {"type": "string", "description": "저장된 화풍 이름 (선택)"},
         "wait_sec": {"type": "integer", "minimum": 0, "maximum": 1200,
                      "description": "끝날 때까지 기다릴 최대 초 (기본 120). 넘으면 job_status 로 확인"}},
         ["image_ids", "steps"])},
    {"name": "job_status", "description": "generate 작업의 진행 상황과 결과.",
     "inputSchema": _schema({"job_id": {"type": "string", "description": "없으면 이번 연결의 모든 작업"},
                             "wait_sec": {"type": "integer", "minimum": 0, "maximum": 1200}})},
    {"name": "cancel_job", "description": "대기 중인 단계를 취소합니다 (이미 보낸 요청은 끝까지 진행될 수 있음).",
     "inputSchema": _schema({"job_id": {"type": "string"}}, ["job_id"])},
    {"name": "rename_images", "description": "라이브러리 안의 이름·태그를 바꿉니다 (드라이브 동기화로 폰에도 반영).",
     "inputSchema": _schema({"items": {"type": "array", "items": _schema(
         {"id": {"type": "integer"}, "name": {"type": "string"}, "tags": {"type": "string"}}, ["id"])}}, ["items"])},
    {"name": "export_images", "description": (
        "이미지 파일을 원하는 폴더에 원하는 파일 이름으로 복사합니다 (네이밍 규칙 정리용, 라이브러리는 그대로). "
        "같은 이름 파일이 있으면 overwrite 가 true 일 때만 덮어씁니다."),
     "inputSchema": _schema({"dest_folder": {"type": "string"},
                             "items": {"type": "array", "items": _schema(
                                 {"id": {"type": "integer"}, "filename": {"type": "string"}}, ["id", "filename"])},
                             "overwrite": {"type": "boolean"}}, ["dest_folder", "items"])},
    {"name": "pinterest_list", "description": (
        "핀터레스트 공개 보드의 최근 핀(약 25개) 목록: pin_id, 날짜, 제목, 이미 가져왔는지. 무료(생성 없음). "
        "board 는 보드 주소 또는 '사용자/보드'. 비우면 프로그램에 마지막으로 쓴 보드."),
     "inputSchema": _schema({"board": {"type": "string"},
                             "since": {"type": "string", "description": "이 날짜(YYYY-MM-DD) 이후 핀만"}})},
    {"name": "pinterest_import", "description": (
        "핀터레스트 핀을 원본으로 가져오고(이미 가져온 핀은 건너뜀) 원하면 steps 를 이어서 생성합니다. "
        "confirm 이 false(기본)면 아무것도 하지 않고 장수·예상 비용만 돌려줍니다 → 사용자에게 보여 주고 OK 받은 뒤 confirm=true. "
        "생성 장수·하루 상한은 generate 와 같습니다."),
     "inputSchema": _schema({
         "board": {"type": "string"}, "pin_ids": {"type": "array", "items": {"type": "string"}},
         "since": {"type": "string", "description": "pin_ids 대신: 이 날짜(YYYY-MM-DD) 이후 핀 전부"},
         "project": {"type": "string", "description": "넣을 프로젝트 (기본 '핀터레스트')"},
         "steps": {"type": "array", "items": {"type": "string", "enum": list(PIPELINE_FUNCTIONS)}},
         "confirm": {"type": "boolean"}, "wait_sec": {"type": "integer", "minimum": 0, "maximum": 1200}})},
    {"name": "sync_now", "description": "구글 드라이브와 지금 동기화 (폰에서 보낸 원본 받기 + 결과 올리기).",
     "inputSchema": _schema({})},
]


class ToolError(Exception):
    pass


class Content(list):
    """도구가 MCP 콘텐츠(텍스트·이미지 블록)를 직접 돌려줄 때 쓰는 표시. 그 밖의 값은 JSON 텍스트로 보낸다."""


def _text(obj) -> dict:
    return {"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1)}


def _image_content(path: Path, max_px: int) -> dict | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, "PNG")
        return {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/png"}
    except Exception:
        return None


class Server:
    def __init__(self, ws_factory=None, out=None):
        self._ws_factory = ws_factory
        self._ws = None
        self._out = out
        self._out_lock = threading.Lock()
        self.jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._queue: list[str] = []
        self._cv = threading.Condition()
        self._worker = None
        # 도구 호출은 전부 이 스레드 하나에서 처리한다 (SQLite 연결을 한 스레드에서만 쓰기 위해).
        # 읽기 루프는 그동안에도 ping 등에 응답한다.
        from concurrent.futures import ThreadPoolExecutor
        self._tools = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-tool")

    # ---- 워크스페이스 -------------------------------------------------------
    @property
    def ws(self):
        if self._ws is None:
            if self._ws_factory:
                self._ws = self._ws_factory()
            else:
                from .workspace import Workspace
                self._ws = Workspace()
        return self._ws

    def _fresh_settings(self):
        """프로그램 창에서 바꾼 설정(켜기/한도)을 매번 다시 읽는다."""
        from .config import Settings
        try:
            fresh = Settings(self.ws.settings.path)
            self.ws.settings.data.update(fresh.data)
        except Exception:
            pass

    def _require_enabled(self):
        self._fresh_settings()
        if not self.ws.settings.get("mcp_enabled"):
            raise ToolError("V-Concept Studio 프로그램의 [설정 > Claude 연결]에서 'Claude Code 연결 허용'을 켜야 사용할 수 있습니다.")

    # ---- 비용 상한 ----------------------------------------------------------
    def _mcp_spent_today(self) -> float:
        r = self.ws.db._one("SELECT COALESCE(SUM(est_cost_usd),0) c FROM usage_log WHERE source='mcp' "
                            "AND created_at>=? AND status IN ('ok','unknown')", (self.ws.day_start(),))
        return float(r["c"] if r else 0)

    def _reserved(self) -> float:
        with self._jobs_lock:
            return sum(j["reserved"] for j in self.jobs.values() if j["state"] in ("queued", "running"))

    def _estimate(self, model, quality, size, function) -> float:
        try:
            est = self.ws.catalog.estimate(model, quality, size, self.ws.default_prompt(function), 1, self.ws.db)
            v = est.for_budget()
            return float(v) if v is not None else FALLBACK_COST_PER_IMAGE
        except Exception:
            return FALLBACK_COST_PER_IMAGE

    # ---- 도구 ---------------------------------------------------------------
    def call_tool(self, name: str, args: dict) -> list:
        fn = getattr(self, "tool_" + name, None)
        if fn is None:
            raise ToolError(f"없는 도구입니다: {name}")
        if name != "status":
            self._require_enabled()
        out = fn(**(args or {}))
        return list(out) if isinstance(out, Content) else [_text(out)]

    def tool_status(self):
        self._fresh_settings()
        from . import keystore
        from .sync.account import is_connected
        s = self.ws.settings
        inbox = self.ws.db._one("SELECT COUNT(*) n FROM images WHERE inbox='new'")["n"]
        with self._jobs_lock:
            running = [{"job_id": k, "state": j["state"], "done": j["done"], "total": j["total"]}
                       for k, j in self.jobs.items() if j["state"] in ("queued", "running")]
        return {"app": f"{APP_NAME} {APP_VERSION}", "claude_connection_enabled": bool(s.get("mcp_enabled")),
                "api_key_registered": bool(keystore.get_key()), "demo_mode": self.ws.demo_mode,
                "max_images_per_call": int(s.get("mcp_max_images_per_call") or 0),
                "daily_budget_usd": float(s.get("mcp_daily_budget_usd") or 0),
                "mcp_spent_today_usd_estimate": round(self._mcp_spent_today(), 4),
                "google_drive_connected": is_connected(), "inbox_new": inbox, "running_jobs": running,
                "default_model": self._default_model(), "image_models": self.ws.catalog.model_ids()}

    def tool_list_projects(self):
        out = []
        for p in self.ws.db.list_projects():
            c = self.ws.db._one("SELECT SUM(kind='original') o, SUM(kind='output') r FROM images WHERE project_id=?",
                                (p["id"],))
            out.append({"name": p["name"], "originals": c["o"] or 0, "outputs": c["r"] or 0})
        return out

    def _row_info(self, r) -> dict:
        proj = self.ws.db.get_project(r["project_id"])
        d = {"id": r["id"], "kind": r["kind"], "name": r["name"] or "", "project": proj["name"] if proj else "",
             "path": str(self.ws.image_path(r)), "created": r["created_at"]}
        for k in ("function", "tags", "note", "inbox"):
            if r[k] if k in r.keys() else None:
                d[k] = r[k]
        if r["favorite"] if "favorite" in r.keys() else 0:
            d["favorite"] = True
        return d

    def tool_list_inbox(self, include_done=False, previews=False):
        sql = "SELECT * FROM images WHERE inbox IS NOT NULL" + ("" if include_done else " AND inbox='new'")
        rows = self.ws.db._all(sql + " ORDER BY id", ())
        content = Content([_text({"count": len(rows), "items": [self._row_info(r) for r in rows]} if rows else
                         "받은 원본이 없습니다. (폰에서 보냈다면 sync_now 로 먼저 받아 오세요.)")])
        if previews:
            for r in rows[:12]:
                img = _image_content(self.ws.image_path(r), 256)
                if img:
                    content += [_text(f"id {r['id']}"), img]
        return content

    def tool_mark_inbox_done(self, image_ids):
        n = 0
        for i in image_ids:
            r = self.ws.db.get_image(i)
            if r and r["inbox"]:
                self.ws.db._update("images", i, {"inbox": "done"})
                n += 1
        return f"{n}개를 처리됨으로 표시했습니다."

    def tool_list_images(self, project=None, kind=None, function=None, query=None, limit=50):
        sql, a = "SELECT i.* FROM images i LEFT JOIN projects p ON p.id=i.project_id WHERE i.kind IN ('original','output')", []
        if project:
            sql += " AND p.name=?"
            a.append(project)
        if kind:
            sql += " AND i.kind=?"
            a.append(kind)
        if function:
            sql += " AND i.function=?"
            a.append(function)
        if query:
            sql += " AND (i.name LIKE ? OR i.tags LIKE ? OR i.note LIKE ?)"
            a += [f"%{query}%"] * 3
        sql += " ORDER BY i.id DESC LIMIT ?"
        a.append(int(limit or 50))
        return [self._row_info(r) for r in self.ws.db._all(sql, tuple(a))]

    def tool_show_image(self, image_id):
        r = self.ws.db.get_image(image_id)
        if not r:
            raise ToolError(f"이미지 id {image_id} 가 없습니다.")
        img = _image_content(self.ws.image_path(r), 768)
        if not img:
            raise ToolError("이미지 파일을 열 수 없습니다.")
        return Content([_text(self._row_info(r)), img])

    def _project_id(self, name: str) -> int:
        name = (name or "").strip()
        if not name:
            raise ToolError("프로젝트 이름이 비어 있습니다.")
        p = self.ws.db.get_project_by_name(name)
        return p["id"] if p else self.ws.create_project(name)

    def tool_import_images(self, paths, project):
        pid = self._project_id(project)
        ok, errors = [], []
        for p in paths:
            try:
                ok.append(self.ws.import_original(pid, p))
            except Exception as e:
                errors.append(f"{p}: {e}")
        return {"imported_ids": ok, "errors": errors}

    def tool_rename_images(self, items):
        n = 0
        for it in items:
            if not self.ws.db.get_image(it["id"]):
                continue
            fields = {k: it[k] for k in ("name", "tags") if k in it}
            if fields:
                self.ws.db.update_image(it["id"], **fields)
                n += 1
        self._schedule_sync()
        return f"{n}개의 이름/태그를 바꿨습니다."

    def tool_export_images(self, dest_folder, items, overwrite=False):
        from .workspace import safe_name
        dest = Path(dest_folder)
        if not dest.is_absolute():
            raise ToolError("dest_folder 는 전체 경로(예: C:\\Users\\...\\정리)로 주세요.")
        dest.mkdir(parents=True, exist_ok=True)
        done, skipped = [], []
        for it in items:
            r = self.ws.db.get_image(it["id"])
            src = self.ws.image_path(r) if r else None
            if not src or not src.exists():
                skipped.append(f"id {it['id']}: 파일 없음")
                continue
            stem = safe_name(Path(it["filename"]).stem if Path(it["filename"]).suffix.lower() in (
                ".png", ".jpg", ".jpeg", ".webp") else it["filename"], "image")
            target = dest / (stem + src.suffix.lower())
            if target.exists() and not overwrite:
                skipped.append(f"{target.name}: 이미 있음 (overwrite=true 로 덮어쓰기)")
                continue
            shutil.copyfile(src, target)
            done.append(str(target))
        return {"copied": done, "skipped": skipped}

    # ---- 생성 ---------------------------------------------------------------
    def _default_model(self) -> str:
        ids = self.ws.catalog.model_ids()
        last = self.ws.settings.get("last_model")
        return last if last in ids else (ids[0] if ids else "")

    def tool_generate(self, image_ids, steps, project=None, model=None, quality=None, size=None, background=None,
                      style=None, wait_sec=120):
        s = self.ws.settings
        steps = [x for x in steps]
        bad = [x for x in steps if x not in PIPELINE_FUNCTIONS]
        if bad or not steps:
            raise ToolError(f"steps 에 쓸 수 있는 기능: {', '.join(PIPELINE_FUNCTIONS)} (받은 값: {steps})")
        rows = []
        for i in image_ids:
            r = self.ws.db.get_image(i)
            if not r or not self.ws.image_path(r) or not self.ws.image_path(r).exists():
                raise ToolError(f"이미지 id {i} 가 없거나 파일을 찾을 수 없습니다.")
            rows.append(r)
        if not rows:
            raise ToolError("image_ids 가 비어 있습니다.")
        total = len(rows) * len(steps)
        limit = int(s.get("mcp_max_images_per_call") or 0)
        if total > limit:
            raise ToolError(f"이번 요청은 {total}장인데, Claude 연결 1번에 만들 수 있는 최대 장수는 {limit}장입니다. "
                            "나눠서 요청하거나 프로그램 [설정 > Claude 연결]에서 한도를 올리세요.")
        model = model or self._default_model()
        quality = quality or s.get("last_quality") or "auto"
        size = size or s.get("last_size") or "auto"
        background = background or s.get("last_background") or "auto"
        problems = self.ws.catalog.validate(model, quality, size, background)
        if problems:
            raise ToolError("지원되지 않는 옵션: " + " / ".join(problems))
        style_text = None
        if style:
            st = next((x for x in self.ws.db.list_styles() if x["name"] == style), None)
            if not st:
                raise ToolError(f"'{style}' 화풍이 없습니다. 저장된 화풍: {[x['name'] for x in self.ws.db.list_styles()]}")
            style_text = st["prompt"]
        cost = 0.0 if self.ws.demo_mode else sum(self._estimate(model, quality, size, f) for f in steps) * len(rows)
        if not self.ws.demo_mode:
            from . import keystore
            if not keystore.get_key():
                raise ToolError("OpenAI API 키가 등록되어 있지 않습니다. 프로그램 [설정 > API]에서 등록하세요.")
            daily = float(s.get("mcp_daily_budget_usd") or 0)
            spent = self._mcp_spent_today() + self._reserved()
            if spent + cost > daily:
                raise ToolError(f"하루 상한을 넘습니다: 오늘 Claude 연결로 쓴 추정액 ${spent:.2f} + 이번 요청 최대 약 ${cost:.2f} "
                                f"> 상한 ${daily:.2f}. 프로그램 [설정 > Claude 연결]에서 상한을 올리거나 내일 다시 요청하세요.")
            budget = float(s.get("monthly_budget_usd") or 0)
            if budget > 0:
                month = self.ws.db.usage_summary(self.ws.month_start())["cost"]
                if month + cost > budget:
                    raise ToolError(f"월간 예산을 넘습니다: 이번 달 ${month:.2f} + 이번 요청 약 ${cost:.2f} > 예산 ${budget:.2f}.")
        target_pid = self._project_id(project) if project else None
        job_id = uuid.uuid4().hex[:8]
        job = {"state": "queued", "image_ids": [r["id"] for r in rows], "steps": steps, "project_id": target_pid,
               "opts": dict(model=model, quality=quality, size=size, background=background), "style": style_text,
               "done": 0, "total": total, "results": [], "errors": [], "reserved": cost, "cancel": False,
               "created": time.time()}
        with self._jobs_lock:
            self.jobs[job_id] = job
        self._start_worker()
        with self._cv:
            self._queue.append(job_id)
            self._cv.notify_all()
        log.info("mcp generate queued %s: images=%s steps=%s est=%.3f", job_id, job["image_ids"], steps, cost)
        return self._job_report(job_id, wait_sec, intro=f"작업 {job_id}: {total}장 (추정 최대 ${cost:.2f})")

    def tool_job_status(self, job_id=None, wait_sec=0):
        if job_id:
            if job_id not in self.jobs:
                raise ToolError(f"작업 {job_id} 가 없습니다 (MCP 서버를 다시 켜면 이전 작업 목록은 사라집니다. list_images 로 결과를 확인하세요).")
            return self._job_report(job_id, wait_sec)
        with self._jobs_lock:
            return [{"job_id": k, "state": j["state"], "done": j["done"], "total": j["total"],
                     "result_ids": [x["id"] for x in j["results"]], "errors": j["errors"]} for k, j in self.jobs.items()]

    def tool_cancel_job(self, job_id):
        j = self.jobs.get(job_id)
        if not j:
            raise ToolError(f"작업 {job_id} 가 없습니다.")
        j["cancel"] = True
        return "취소를 요청했습니다. 이미 보낸 요청은 끝까지 진행될 수 있습니다."

    def _job_report(self, job_id, wait_sec, intro=None):
        deadline = time.time() + max(0, min(int(wait_sec or 0), 1200))
        j = self.jobs[job_id]
        while j["state"] in ("queued", "running") and time.time() < deadline:
            time.sleep(0.5)
        info = {"job_id": job_id, "state": j["state"], "done": j["done"], "total": j["total"],
                "results": j["results"], "errors": j["errors"]}
        if j["state"] in ("queued", "running"):
            info["hint"] = "아직 진행 중입니다. job_status 로 다시 확인하세요."
        content = Content(([_text(intro)] if intro else []) + [_text(info)])
        for res in j["results"][-6:]:
            img = _image_content(Path(res["path"]), 384)
            if img:
                content.append(img)
        return content

    def _start_worker(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work_loop, name="mcp-jobs", daemon=True)
            self._worker.start()

    def _work_loop(self):
        from .db import Database
        ws = copy.copy(self.ws)                 # 작업 스레드 전용 DB 연결
        ws.db = Database(self.ws.paths.db_file)
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                job_id = self._queue.pop(0)
            try:
                self._run_job(ws, job_id)
            except Exception:
                log.exception("mcp job crashed")
                self.jobs[job_id]["errors"].append("예상하지 못한 오류: " + traceback.format_exc(limit=2)[-300:])
                self.jobs[job_id]["state"] = "failed"

    def _run_job(self, ws, job_id):
        from . import jobs as jobs_core
        from .functions import compose_prompt
        from .services.image_service import GenerationError
        j = self.jobs[job_id]
        j["state"] = "running"
        per_image = j["reserved"] / max(1, j["total"])
        for iid in j["image_ids"]:
            current = ws.db.get_image(iid)
            for step in j["steps"]:
                if j["cancel"]:
                    j["errors"].append("취소됨")
                    break
                pid = j["project_id"] or current["project_id"]
                job = jobs_core.GenJob(
                    project_id=pid, function=step, prompt=compose_prompt(ws.default_prompt(step), j["style"]),
                    preset_id=None, preset_name="기본", input_paths=[ws.image_path(current)],
                    source_image_id=current["id"], source="mcp", **j["opts"])
                try:
                    new_id = jobs_core.run(ws, job)
                except GenerationError as e:
                    j["errors"].append(f"이미지 {iid} · {step}: {e.message}")
                    break
                finally:
                    j["done"] += 1
                    j["reserved"] = max(0.0, j["reserved"] - per_image)
                row = ws.db.get_image(iid)
                if row and row["inbox"] == "new":
                    ws.db._update("images", iid, {"inbox": "done"})
                current = ws.db.get_image(new_id)
                j["results"].append({"id": new_id, "source_id": iid, "function": step, "path": str(ws.image_path(current))})
            if j["cancel"]:
                break
        j["reserved"] = 0.0
        j["state"] = "cancelled" if j["cancel"] else ("done" if not j["errors"] else
                                                       ("partial" if j["results"] else "failed"))
        if j["results"]:
            self._schedule_sync(ws)

    # ---- 동기화 -------------------------------------------------------------
    def _sync(self, ws=None):
        from .sync.account import is_connected, make_drive
        from .sync.engine import SyncEngine
        ws = ws or self.ws
        if not is_connected():
            raise ToolError("구글 드라이브가 연결되어 있지 않습니다. 프로그램 [설정 > 구글 드라이브]에서 먼저 연결하세요.")
        return SyncEngine(ws, make_drive(ws)).sync()

    def _schedule_sync(self, ws=None):
        from .sync.account import is_connected
        if not (self.ws.settings.get("sync_auto") and is_connected()):
            return

        def run():
            from .db import Database
            w = copy.copy(self.ws)
            w.db = Database(self.ws.paths.db_file)
            try:
                st = self._sync(w)
                log.info("mcp auto sync: %s", st.summary())
            except Exception as e:
                log.warning("mcp auto sync failed: %s", e)
            finally:
                w.db.close()
        threading.Thread(target=run, name="mcp-sync", daemon=True).start()

    # ---- 핀터레스트 (v0.6.0) -------------------------------------------------
    pinterest_get = None    # 테스트에서 가짜 네트워크로 바꿈

    def _pins(self, board, since):
        from . import pinterest as pt
        board = (board or self.ws.settings.get("pinterest_last_board") or "").strip()
        if not board:
            raise ToolError("board 를 넣어 주세요 (예: https://www.pinterest.com/사용자이름/보드이름/).")
        try:
            pins = pt.fetch_board(board, get=self.pinterest_get or pt.http_get)
            return board, pt.filter_since(pins, pt.parse_since(since))
        except pt.PinterestError as e:
            raise ToolError(str(e)) from e

    def tool_pinterest_list(self, board=None, since=None):
        board, pins = self._pins(board, since)
        seen = self.ws.db.pinterest_seen()
        return {"board": board, "count": len(pins), "pins": [
            {"pin_id": p.pin_id, "date": p.date_text, "title": p.title[:80], "link": p.link,
             "imported_image_id": seen.get(p.pin_id)} for p in pins],
            "note": "RSS 의 최근 핀만 보입니다. 날짜는 핀 날짜(보드에 추가한 날짜와 다를 수 있음)."}

    def tool_pinterest_import(self, board=None, pin_ids=None, since=None, project=None, steps=None, confirm=False,
                              wait_sec=120):
        from . import pinterest as pt
        steps = list(steps or [])
        bad = [x for x in steps if x not in PIPELINE_FUNCTIONS]
        if bad:
            raise ToolError(f"steps 에 쓸 수 있는 기능: {', '.join(PIPELINE_FUNCTIONS)}")
        if not pin_ids and not since:
            raise ToolError("pin_ids 또는 since 중 하나를 넣어 주세요.")
        board, pins = self._pins(board, since)
        if pin_ids:
            want = set(pin_ids)
            pins = [p for p in pins if p.pin_id in want]
        seen = self.ws.db.pinterest_seen()
        skipped = [p.pin_id for p in pins if p.pin_id in seen]
        pins = [p for p in pins if p.pin_id not in seen]
        s = self.ws.settings
        total = len(pins) * len(steps)
        model = self._default_model()
        q, z = s.get("last_quality") or "auto", s.get("last_size") or "auto"
        cost = 0.0 if self.ws.demo_mode else sum(self._estimate(model, q, z, f) for f in steps) * len(pins)
        preview = {"board": board, "new_pins": len(pins), "already_imported": skipped, "steps": steps,
                   "generate_images": total, "estimated_cost_usd": round(cost, 2),
                   "limit_per_call": int(s.get("mcp_max_images_per_call") or 0),
                   "daily_budget_usd": float(s.get("mcp_daily_budget_usd") or 0),
                   "spent_today_usd": round(self._mcp_spent_today(), 2)}
        if not confirm:
            preview["hint"] = ("아직 아무것도 하지 않았습니다. 이 장수·예상 비용을 사용자에게 보여 주고, "
                               "OK 를 받으면 같은 값에 confirm=true 를 붙여 다시 부르세요.")
            if total > preview["limit_per_call"]:
                preview["warning"] = "생성 장수가 1번 최대 장수를 넘어 생성은 거절됩니다. 나눠서 요청하세요."
            return preview
        if not pins:
            return {**preview, "imported_ids": [], "message": "새로 가져올 핀이 없습니다."}
        if steps and total > preview["limit_per_call"]:
            raise ToolError(f"생성 {total}장은 1번 최대 장수 {preview['limit_per_call']}장을 넘습니다. 핀을 나눠서 요청하세요.")
        pid = self._project_id(project or "핀터레스트")
        get = self.pinterest_get or pt.http_get
        ids, errors = [], []
        key = pt.board_key(board)
        for p in pins:
            try:
                data, url = pt.download_image(p, get=get)
                ids.append(self.ws.import_pin(pid, p, data, url, key))
            except Exception as e:
                errors.append(f"{p.pin_id}: {e}")
        out = {"imported_ids": ids, "errors": errors, "already_imported": skipped,
               "note": "가져온 이미지는 '외부 출처' 태그 — 공부·참고용, 판매·커미션·게시 금지."}
        if not steps or not ids:
            return out
        try:
            report = self.tool_generate(ids, steps, project=project or "핀터레스트", wait_sec=wait_sec)
        except ToolError as e:   # 가져오기는 끝났고 생성만 거절됨
            return {**out, "generate_error": str(e)}
        return Content([_text(out)] + list(report))

    def tool_sync_now(self):
        st = self._sync()
        return {"summary": st.summary(), "errors": st.errors[:10],
                "inbox_new": self.ws.db._one("SELECT COUNT(*) n FROM images WHERE inbox='new'")["n"]}

    # ---- JSON-RPC ------------------------------------------------------------
    def handle(self, msg: dict) -> dict | None:
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if mid is None:                      # 알림(notification): 응답 없음
            return None
        try:
            if method == "initialize":
                want = params.get("protocolVersion")
                result = {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "v-concept-studio", "version": APP_VERSION},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    content = self.call_tool(params.get("name"), params.get("arguments") or {})
                    result = {"content": content, "isError": False}
                except ToolError as e:
                    result = {"content": [_text(str(e))], "isError": True}
                except TypeError as e:
                    result = {"content": [_text(f"인자가 잘못되었습니다: {e}")], "isError": True}
                except Exception as e:
                    log.exception("tool %s failed", params.get("name"))
                    result = {"content": [_text(f"오류: {type(e).__name__}: {e}")], "isError": True}
            elif method in ("resources/list", "prompts/list"):
                result = {method.split("/")[0]: []}
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as e:
            log.exception("request failed")
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._out_lock:
            self._out.write(data)
            self._out.flush()

    def serve(self, inp):
        """한 줄씩 읽어 처리. 오래 걸리는 도구는 따로 스레드에서 돌려서 ping 등에 계속 응답한다."""
        for raw in inp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if isinstance(msg, list):
                for m in msg:
                    r = self.handle(m)
                    if r:
                        self.send(r)
                continue
            if msg.get("method") == "tools/call":
                self._tools.submit(self._reply, msg)
            else:
                self._reply(msg)

    def _reply(self, msg):
        r = self.handle(msg)
        if r:
            self.send(r)


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr            # 실수로 print 해도 프로토콜을 깨지 않게
    try:
        from .config import AppPaths, Settings
        from .logging_setup import setup_logging
        setup_logging(AppPaths(Settings().get("data_root")).logs)
    except Exception:
        pass
    log.info("MCP server start %s", APP_VERSION)
    server = Server(out=out)
    try:
        server.serve(sys.stdin.buffer)
    except KeyboardInterrupt:
        pass
    server._tools.shutdown(wait=True)
    log.info("MCP server stop")
    return 0

"""PC 프로그램 동기화 엔진: 생성 결과 + 에셋 ↔ 구글 드라이브 폴더.

규칙은 crs/sync/__init__.py 설명 참고. 웹앱(web/js/sync.js)과 같은 형식을 쓴다.
"""
from __future__ import annotations

import io
import json
import logging
import uuid
from dataclasses import dataclass, field

from PIL import Image

from ..db import now_iso, ts_key
from . import META_VERSION

log = logging.getLogger(__name__)
INCOMING_PROJECT = "웹앱에서 받음"
INBOX_PROJECT = "폰에서 받은 원본"     # 폰 웹앱에서 [PC로 보내기] 한 원본의 기본 프로젝트


@dataclass
class SyncStats:
    uploaded: int = 0
    downloaded: int = 0
    meta_pushed: int = 0
    meta_pulled: int = 0
    deleted_local: int = 0
    trashed_remote: int = 0
    inbox_received: int = 0
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"올림 {self.uploaded}", f"받음 {self.downloaded}"]
        if self.inbox_received:
            parts.append(f"폰 원본 {self.inbox_received}")
        if self.meta_pushed or self.meta_pulled:
            parts.append(f"정보 갱신 {self.meta_pushed + self.meta_pulled}")
        if self.deleted_local or self.trashed_remote:
            parts.append(f"삭제 반영 {self.deleted_local + self.trashed_remote}")
        if self.errors:
            parts.append(f"오류 {len(self.errors)}")
        return " · ".join(parts)


def _parse_meta(f: dict) -> dict | None:
    try:
        meta = json.loads(f.get("description") or "{}")
    except ValueError:
        return None
    uid = meta.get("uid") or (f.get("appProperties") or {}).get("vc_uid")
    if not uid or meta.get("kind") not in ("output", "asset", "inbox"):
        return None
    meta["uid"] = uid
    return meta


class SyncEngine:
    def __init__(self, ws, drive):
        self.ws = ws
        self.db = ws.db
        self.drive = drive

    # ---- 로컬 → 메타데이터 -------------------------------------------------
    def _meta_of(self, table, row) -> dict:
        updated = row.get("meta_updated") or row.get("created_at")
        if table == "images":
            proj = self.db.get_project(row["project_id"])
            return {"v": META_VERSION, "uid": row["uid"], "kind": "output", "function": row.get("function") or "",
                    "category": "", "name": row.get("name") or "", "tags": row.get("tags") or "",
                    "note": row.get("note") or "", "favorite": int(row.get("favorite") or 0),
                    "created": row["created_at"], "updated": updated, "model": row.get("model") or "",
                    "quality": row.get("quality") or "", "size": row.get("size") or "",
                    "background": row.get("background") or "", "project": proj["name"] if proj else "",
                    "request_id": row.get("request_id") or ""}
        return {"v": META_VERSION, "uid": row["uid"], "kind": "asset", "function": "", "category": row["category"],
                "name": row.get("name") or "", "tags": row.get("tags") or "", "note": row.get("note") or "",
                "favorite": 0, "created": row["created_at"], "updated": updated, "model": "", "quality": "",
                "size": "", "background": "", "project": "", "request_id": ""}

    def _ensure_uid(self, table, row) -> dict:
        if not row.get("uid"):
            row["uid"] = uuid.uuid4().hex
            self.db._update(table, row["id"], {"uid": row["uid"]})
        return row

    def _png_bytes(self, table, row) -> bytes:
        path = self.ws.paths.abs(row["rel_path"])
        with Image.open(path) as im:
            if (im.format or "").upper() == "PNG":
                return path.read_bytes()
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue()

    # ---- 받기 --------------------------------------------------------------
    def _incoming(self, meta, data: bytes):
        img = Image.open(io.BytesIO(data))
        img.load()
        if meta["kind"] == "asset":
            from ..functions import CATEGORIES
            cat = meta.get("category") if meta.get("category") in CATEGORIES else "Character"
            aid = self.ws.register_asset(img, cat, meta.get("name") or "", meta.get("tags") or "", meta.get("note") or "")
            self.db._update("assets", aid, {"uid": meta["uid"], "meta_updated": meta.get("updated"),
                                            "synced_meta": meta.get("updated")})
            return
        pname = meta.get("project") or INCOMING_PROJECT
        proj = self.db.get_project_by_name(pname)
        pid = proj["id"] if proj else self.ws.create_project(pname)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        iid = self.ws.save_output(
            pid, buf.getvalue(), function=meta.get("function") or "", source_image_id=None, prompt="",
            preset_id=None, preset_name="", model=meta.get("model") or "", quality=meta.get("quality") or "",
            size=meta.get("size") or "", background=meta.get("background") or "", params={"synced": True},
            request_id=meta.get("request_id") or None, usage=None, est_cost_usd=None)
        self.db._update("images", iid, {"uid": meta["uid"], "name": meta.get("name") or "",
                                        "tags": meta.get("tags") or "", "note": meta.get("note") or "",
                                        "favorite": int(meta.get("favorite") or 0),
                                        "created_at": meta.get("created") or now_iso(),
                                        "meta_updated": meta.get("updated"), "synced_meta": meta.get("updated")})
        self.ws.write_project_json(pid)

    def _apply_meta(self, table, row, meta):
        fields = {"name": meta.get("name") or "", "tags": meta.get("tags") or "", "note": meta.get("note") or "",
                  "meta_updated": meta.get("updated"), "synced_meta": meta.get("updated")}
        if table == "images":
            fields["favorite"] = int(meta.get("favorite") or 0)
        elif meta.get("category"):
            fields["category"] = meta["category"]
        self.db._update(table, row["id"], fields)

    # ---- 동기화 ------------------------------------------------------------
    def sync(self, progress=None) -> SyncStats:
        """프로그램 창과 MCP 모드가 동시에 동기화하지 않도록 파일 잠금 안에서 실행한다."""
        from .lock import sync_lock
        with sync_lock(self.ws.paths.root):
            return self._sync(progress)

    def _receive_inbox(self, f: dict, meta: dict, st: SyncStats, say):
        """폰에서 보낸 원본 1장: PC 프로젝트에 원본으로 넣고 '받은 원본' 표시 → 드라이브 사본은 휴지통으로."""
        uid = meta["uid"]
        if not self.db.find_by_uid("images", uid):
            say(f"폰 원본 받는 중: {meta.get('name') or f.get('name')}")
            data = self.drive.download(f["id"])
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                pil = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im.copy()
            name = (meta.get("project") or "").strip() or INBOX_PROJECT
            proj = self.db.get_project_by_name(name)
            pid = proj["id"] if proj else self.ws.create_project(name)
            iid = self.ws.import_original(pid, pil, name=meta.get("name") or "폰 원본")
            self.db._update("images", iid, {"uid": uid, "inbox": "new", "note": meta.get("note") or "",
                                            "tags": meta.get("tags") or ""})
            st.inbox_received += 1
        self.drive.trash(f["id"])

    def _sync(self, progress=None) -> SyncStats:
        st = SyncStats()
        say = progress or (lambda text: None)
        say("드라이브 폴더 확인 중…")
        folder = self.drive.find_or_create_folder()
        if self.ws.settings.get("sync_folder_id") != folder:
            # 다른 계정/폴더로 바뀜 → 예전 연결 정보는 버리고 모두 새로 올린다 (로컬 항목은 지우지 않음)
            self.db.reset_sync_links()
            self.ws.settings.set("sync_folder_id", folder)
        say("드라이브 목록 읽는 중…")
        remote = {}
        for f in self.drive.list_items(folder):
            meta = _parse_meta(f)
            if not meta:
                continue
            if meta["kind"] == "inbox":
                try:
                    self._receive_inbox(f, meta, st, say)
                except Exception as e:
                    st.errors.append(f"폰 원본 받기 실패({meta.get('name') or meta['uid'][:8]}): {e}")
                continue
            remote[meta["uid"]] = (f, meta)

        # 1) 이 PC에서 지운 항목 → 드라이브 휴지통
        for t in self.db.tombstones():
            try:
                if t["drive_id"]:
                    self.drive.trash(t["drive_id"])
                    st.trashed_remote += 1
                remote.pop(t["uid"], None)
                self.db.drop_tombstone(t["uid"])
            except Exception as e:
                st.errors.append(f"삭제 반영 실패: {e}")

        local = {}
        for table in ("images", "assets"):
            for row in self.db.sync_rows(table):
                local[row["uid"] or f"__new_{table}_{row['id']}"] = (table, row)

        # 2) 드라이브에만 있는 항목 받기 / 양쪽에 있으면 정보 맞추기
        for uid, (f, meta) in remote.items():
            if uid not in local:
                try:
                    say(f"받는 중: {meta.get('name') or f.get('name')}")
                    self._incoming(meta, self.drive.download(f["id"]))
                    row = self.db.find_by_uid("images", uid) or self.db.find_by_uid("assets", uid)
                    table = "images" if self.db.find_by_uid("images", uid) else "assets"
                    self.db._update(table, row["id"], {"drive_id": f["id"]})
                    st.downloaded += 1
                except Exception as e:
                    st.errors.append(f"받기 실패({meta.get('name') or uid[:8]}): {e}")
                continue
            table, row = local[uid]
            if row.get("drive_id") != f["id"]:
                self.db._update(table, row["id"], {"drive_id": f["id"]})
            r_upd = ts_key(meta.get("updated"))
            l_upd = ts_key(row.get("meta_updated") or row.get("created_at"))
            dirty = (row.get("meta_updated") or None) != (row.get("synced_meta") or None)
            try:
                if dirty and l_upd >= r_upd:
                    self.drive.update_meta(f["id"], self._meta_of(table, row))
                    self.db._update(table, row["id"], {"synced_meta": row.get("meta_updated")})
                    st.meta_pushed += 1
                elif r_upd > l_upd:
                    self._apply_meta(table, row, meta)
                    st.meta_pulled += 1
                elif dirty:
                    self.db._update(table, row["id"], {"synced_meta": row.get("meta_updated")})
            except Exception as e:
                st.errors.append(f"정보 갱신 실패: {e}")

        # 3) 드라이브에서 지워진 항목(다른 기기에서 삭제) → 이 PC에서도 지움
        for uid, (table, row) in local.items():
            if row.get("drive_id") and uid not in remote:
                if table == "images":
                    self.ws.delete_image(row["id"], tombstone=False)
                else:
                    self.ws.delete_asset(row["id"], tombstone=False)
                st.deleted_local += 1

        # 4) 아직 안 올린 항목 올리기
        for key, (table, row) in local.items():
            if row.get("drive_id") or (row.get("uid") in remote):
                continue
            try:
                row = self._ensure_uid(table, dict(row))
                say(f"올리는 중: {row.get('name') or row['uid'][:8]}")
                meta = self._meta_of(table, row)
                name = f"{(meta['function'] or meta['category'] or 'item')}_{row['uid'][:8]}.png"
                res = self.drive.upload(folder, name, self._png_bytes(table, row), meta)
                self.db._update(table, row["id"], {"drive_id": res["id"], "synced_meta": row.get("meta_updated")})
                st.uploaded += 1
            except Exception as e:
                st.errors.append(f"올리기 실패({row.get('name') or row['id']}): {e}")
        self.ws.settings.set("last_sync", now_iso())
        log.info("sync done: %s", st.summary())
        return st

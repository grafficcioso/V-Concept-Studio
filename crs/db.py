"""SQLite 메타데이터 저장소. 이미지 파일은 디스크에, 연결 관계는 여기에 둔다.

모든 경로는 데이터 루트 기준 상대 경로다 (폴더 이동 대응). API 키는 저장하지 않는다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, folder TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS presets(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, function TEXT NOT NULL, prompt TEXT NOT NULL,
  model TEXT, quality TEXT, size TEXT, background TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS images(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,              -- 'original' | 'output' | 'part_input'(파트 사면도용으로 잘라낸 입력)
  rel_path TEXT NOT NULL, thumb_path TEXT,
  name TEXT DEFAULT '', tags TEXT DEFAULT '', note TEXT DEFAULT '', favorite INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  source_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
  function TEXT, preset_id INTEGER REFERENCES presets(id) ON DELETE SET NULL, preset_name TEXT,
  prompt TEXT, model TEXT, quality TEXT, size TEXT, background TEXT,
  params_json TEXT, request_id TEXT, usage_json TEXT, est_cost_usd REAL);
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY, rel_path TEXT NOT NULL, thumb_path TEXT,
  category TEXT NOT NULL, name TEXT DEFAULT '', tags TEXT DEFAULT '', note TEXT DEFAULT '',
  source_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
  source_original_id INTEGER REFERENCES images(id) ON DELETE SET NULL,
  preset_id INTEGER REFERENCES presets(id) ON DELETE SET NULL,
  params_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_log(
  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
  project_id INTEGER, image_id INTEGER, function TEXT,
  model TEXT, quality TEXT, size TEXT, n_inputs INTEGER DEFAULT 0,
  status TEXT NOT NULL,            -- 'ok' | 'failed' | 'unknown'(과금 여부 불명) | 'demo'
  input_text_tokens INTEGER, input_image_tokens INTEGER, output_tokens INTEGER,
  usage_json TEXT, est_cost_usd REAL, request_id TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS compositions(
  id INTEGER PRIMARY KEY, project_id INTEGER, name TEXT NOT NULL, json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS styles(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL, summary TEXT DEFAULT '',
  source_image_id INTEGER REFERENCES images(id) ON DELETE SET NULL, model TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_images_project ON images(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_log(created_at);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- helpers -------------------------------------------------------
    def _all(self, sql, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    def _insert(self, table, fields: dict) -> int:
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        cur = self.conn.execute(f"INSERT INTO {table}({cols}) VALUES({marks})", list(fields.values()))
        self.conn.commit()
        return cur.lastrowid

    def _update(self, table, row_id, fields: dict):
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*fields.values(), row_id])
        self.conn.commit()

    def _delete(self, table, row_id):
        self.conn.execute(f"DELETE FROM {table} WHERE id=?", (row_id,))
        self.conn.commit()

    # ---- projects ------------------------------------------------------
    def create_project(self, name: str, folder: str) -> int:
        return self._insert("projects", {"name": name, "folder": folder, "created_at": now_iso()})

    def list_projects(self):
        return self._all("SELECT * FROM projects ORDER BY id")

    def get_project(self, pid):
        return self._one("SELECT * FROM projects WHERE id=?", (pid,))

    # ---- images --------------------------------------------------------
    def add_image(self, **fields) -> int:
        fields.setdefault("created_at", now_iso())
        return self._insert("images", fields)

    def get_image(self, image_id):
        return self._one("SELECT * FROM images WHERE id=?", (image_id,)) if image_id else None

    def list_images(self, project_id, kind=None, favorites_only=False, search=""):
        sql, args = "SELECT * FROM images WHERE project_id=?", [project_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if favorites_only:
            sql += " AND favorite=1"
        if search:
            sql += " AND (name LIKE ? OR tags LIKE ? OR note LIKE ?)"
            args += [f"%{search}%"] * 3
        return self._all(sql + " ORDER BY id DESC", args)

    def update_image(self, image_id, **fields):
        self._update("images", image_id, fields)

    def image_references(self, image_id) -> dict:
        """삭제 전 참조 무결성 확인용."""
        derived = self._one("SELECT COUNT(*) c FROM images WHERE source_image_id=?", (image_id,))["c"]
        assets = self._one("SELECT COUNT(*) c FROM assets WHERE source_image_id=? OR source_original_id=?",
                           (image_id, image_id))["c"]
        return {"derived_outputs": derived, "assets": assets}

    def delete_image(self, image_id):
        self._delete("images", image_id)

    # ---- presets -------------------------------------------------------
    def list_presets(self, function=None):
        if function:
            return self._all("SELECT * FROM presets WHERE function=? ORDER BY name", (function,))
        return self._all("SELECT * FROM presets ORDER BY function, name")

    def get_preset(self, preset_id):
        return self._one("SELECT * FROM presets WHERE id=?", (preset_id,)) if preset_id else None

    def add_preset(self, name, function, prompt, model=None, quality=None, size=None, background=None) -> int:
        t = now_iso()
        return self._insert("presets", {"name": name, "function": function, "prompt": prompt, "model": model,
                                        "quality": quality, "size": size, "background": background,
                                        "created_at": t, "updated_at": t})

    def update_preset(self, preset_id, **fields):
        fields["updated_at"] = now_iso()
        self._update("presets", preset_id, fields)

    def delete_preset(self, preset_id):
        self._delete("presets", preset_id)

    # ---- assets --------------------------------------------------------
    def add_asset(self, **fields) -> int:
        fields.setdefault("created_at", now_iso())
        return self._insert("assets", fields)

    def get_asset(self, asset_id):
        return self._one("SELECT * FROM assets WHERE id=?", (asset_id,)) if asset_id else None

    def list_assets(self, category=None, search=""):
        sql, args = "SELECT * FROM assets WHERE 1=1", []
        if category:
            sql += " AND category=?"
            args.append(category)
        if search:
            sql += " AND (name LIKE ? OR tags LIKE ? OR note LIKE ?)"
            args += [f"%{search}%"] * 3
        return self._all(sql + " ORDER BY id DESC", args)

    def update_asset(self, asset_id, **fields):
        self._update("assets", asset_id, fields)

    def delete_asset(self, asset_id):
        self._delete("assets", asset_id)

    def compositions_using_asset(self, asset_id) -> list[str]:
        names = []
        for row in self._all("SELECT name, json FROM compositions"):
            try:
                layers = json.loads(row["json"]).get("layers", [])
            except ValueError:
                continue
            if any(l.get("asset_id") == asset_id for l in layers):
                names.append(row["name"])
        return names

    # ---- usage ---------------------------------------------------------
    def add_usage(self, **fields) -> int:
        fields.setdefault("created_at", now_iso())
        return self._insert("usage_log", fields)

    def usage_rows(self, limit=500):
        return self._all("SELECT * FROM usage_log ORDER BY id DESC LIMIT ?", (limit,))

    def usage_summary(self, since_iso: str) -> dict:
        r = self._one(
            "SELECT COALESCE(SUM(CASE WHEN status='ok' THEN est_cost_usd END),0) cost,"
            " SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok,"
            " SUM(CASE WHEN status='ok' AND est_cost_usd IS NULL THEN 1 ELSE 0 END) ok_uncosted,"
            " SUM(CASE WHEN status='unknown' THEN 1 ELSE 0 END) unknown,"
            " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed"
            " FROM usage_log WHERE created_at>=?", (since_iso,))
        return {k: (v or 0) for k, v in r.items()}

    def output_token_history(self, model, quality, size, limit=20) -> list[int]:
        rows = self._all(
            "SELECT output_tokens FROM usage_log WHERE status='ok' AND model=? AND quality=? AND size=?"
            " AND output_tokens IS NOT NULL ORDER BY id DESC LIMIT ?", (model, quality, size, limit))
        return [r["output_tokens"] for r in rows]

    def input_image_token_history(self, model, limit=20) -> list[float]:
        """입력 이미지 1장당 토큰 수 기록."""
        rows = self._all(
            "SELECT input_image_tokens t, n_inputs n FROM usage_log WHERE status='ok' AND model=?"
            " AND input_image_tokens IS NOT NULL AND n_inputs>0 ORDER BY id DESC LIMIT ?", (model, limit))
        return [r["t"] / r["n"] for r in rows]

    def text_usage_history(self, model, limit=20) -> list[tuple[int, int]]:
        rows = self._all(
            "SELECT input_text_tokens i, output_tokens o FROM usage_log WHERE status='ok' AND function='style_extract'"
            " AND model=? AND output_tokens IS NOT NULL ORDER BY id DESC LIMIT ?", (model, limit))
        return [((r["i"] or 0), r["o"]) for r in rows]

    # ---- styles --------------------------------------------------------
    def add_style(self, name, prompt, summary="", source_image_id=None, model=None) -> int:
        t = now_iso()
        return self._insert("styles", {"name": name, "prompt": prompt, "summary": summary,
                                       "source_image_id": source_image_id, "model": model,
                                       "created_at": t, "updated_at": t})

    def list_styles(self):
        return self._all("SELECT * FROM styles ORDER BY name")

    def get_style(self, style_id):
        return self._one("SELECT * FROM styles WHERE id=?", (style_id,)) if style_id else None

    def update_style(self, style_id, **fields):
        fields["updated_at"] = now_iso()
        self._update("styles", style_id, fields)

    def delete_style(self, style_id):
        self._delete("styles", style_id)

    # ---- compositions --------------------------------------------------
    def save_composition(self, comp_id, project_id, name, data: dict) -> int:
        t = now_iso()
        text = json.dumps(data, ensure_ascii=False)
        if comp_id:
            self._update("compositions", comp_id, {"name": name, "json": text, "updated_at": t,
                                                   "project_id": project_id})
            return comp_id
        return self._insert("compositions", {"project_id": project_id, "name": name, "json": text,
                                             "created_at": t, "updated_at": t})

    def list_compositions(self):
        return self._all("SELECT id, project_id, name, updated_at FROM compositions ORDER BY updated_at DESC")

    def get_composition(self, comp_id):
        return self._one("SELECT * FROM compositions WHERE id=?", (comp_id,))

    def delete_composition(self, comp_id):
        self._delete("compositions", comp_id)

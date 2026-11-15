"""MCP 서버 모드 테스트: 공식 MCP 클라이언트(SDK)로 실제 프로세스를 띄워 확인 + 비용 상한·받은 원본."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from conftest import FAKE_KEY, make_mock_client, ok_response

ROOT = Path(__file__).resolve().parent.parent


def _a(obj, *names):
    """MCP SDK 버전에 따라 serverInfo / server_info 처럼 이름이 다르다."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(names)


def _server(ws):
    from crs.mcp_server import Server
    return Server(ws_factory=lambda: ws)


def _text(content):
    return json.loads(content[0]["text"]) if content[0]["text"][:1] in "[{" else content[0]["text"]


def _wait(srv, job_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = srv.jobs[job_id]
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------ 실제 프로세스 + 공식 MCP 클라이언트
def test_mcp_stdio_with_official_client(tmp_path, sample_png):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({
        "data_root": str(tmp_path / "data"), "demo_mode": True, "mcp_enabled": True}), encoding="utf-8")
    env = {**os.environ, "CRS_CONFIG_DIR": str(cfg), "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "main.py"), "--mcp"], env=env, cwd=str(ROOT))
    export_dir = tmp_path / "정리"

    async def scenario():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                assert _a(init, "server_info", "serverInfo").name == "v-concept-studio"
                assert "list_inbox" in (init.instructions or "")
                names = {t.name for t in (await s.list_tools()).tools}
                assert {"status", "generate", "list_inbox", "export_images", "sync_now"} <= names
                st = json.loads((await s.call_tool("status", {})).content[0].text)
                assert st["claude_connection_enabled"] and st["demo_mode"]
                imp = json.loads((await s.call_tool("import_images", {"paths": [str(sample_png)], "project": "캐릭터A"})).content[0].text)
                src = imp["imported_ids"][0]
                res = await s.call_tool("generate", {"image_ids": [src], "steps": ["front_view", "four_views"], "wait_sec": 60})
                assert not _a(res, "is_error", "isError"), res.content[0].text
                info = json.loads(res.content[1].text)
                assert info["state"] == "done" and [x["function"] for x in info["results"]] == ["front_view", "four_views"]
                assert info["results"][1]["source_id"] == src
                assert any(c.type == "image" for c in res.content)          # Claude 가 결과를 직접 볼 수 있다
                ids = [x["id"] for x in info["results"]]
                ex = json.loads((await s.call_tool("export_images", {"dest_folder": str(export_dir), "items": [
                    {"id": ids[0], "filename": "캐릭터A_정면_01"}, {"id": ids[1], "filename": "캐릭터A_사면도_01.png"}]})).content[0].text)
                assert len(ex["copied"]) == 2
                again = json.loads((await s.call_tool("export_images", {"dest_folder": str(export_dir), "items": [
                    {"id": ids[0], "filename": "캐릭터A_정면_01"}]})).content[0].text)
                assert again["copied"] == [] and "이미 있음" in again["skipped"][0]
                await s.call_tool("rename_images", {"items": [{"id": ids[1], "name": "A 사면도", "tags": "완성"}]})
                listed = json.loads((await s.call_tool("list_images", {"project": "캐릭터A", "function": "four_views"})).content[0].text)
                assert listed[0]["name"] == "A 사면도" and listed[0]["tags"] == "완성"
                shown = await s.call_tool("show_image", {"image_id": ids[1]})
                assert shown.content[1].type == "image"
                bad = await s.call_tool("generate", {"image_ids": [src], "steps": ["retouch"]})
                assert _a(bad, "is_error", "isError")

    asyncio.run(asyncio.wait_for(scenario(), 120))
    assert sorted(p.name for p in export_dir.iterdir()) == ["캐릭터A_사면도_01.png", "캐릭터A_정면_01.png"]


# ------------------------------------------------------------------ 켜기·한도
def test_disabled_until_turned_on(ws):
    from crs.mcp_server import ToolError
    srv = _server(ws)
    assert srv.call_tool("status", {})          # 상태는 꺼져 있어도 볼 수 있다
    with pytest.raises(ToolError, match="Claude 연결"):
        srv.call_tool("list_projects", {})
    ws.settings.set("mcp_enabled", True)
    assert srv.call_tool("list_projects", {})[0]["text"] == "[]"
    ws.create_project("X")
    assert json.loads(srv.call_tool("list_projects", {})[0]["text"]) == [{"name": "X", "originals": 0, "outputs": 0}]


def test_cost_limits_and_usage_source(ws, sample_png, monkeypatch):
    from crs import keystore
    from crs.mcp_server import ToolError
    from crs.services.image_service import ImageGenerationService
    keystore.set_key(FAKE_KEY)
    ws._real_service = ImageGenerationService(keystore.get_key, http_client=make_mock_client(lambda r: ok_response()))
    ws.settings.set("mcp_enabled", True)
    srv = _server(ws)
    pid = ws.create_project("P")
    ids = [ws.import_original(pid, sample_png) for _ in range(4)]

    ws.settings.set("mcp_max_images_per_call", 6)
    with pytest.raises(ToolError, match="최대 장수는 6장"):
        srv.call_tool("generate", {"image_ids": ids, "steps": ["front_view", "four_views"]})   # 8장

    monkeypatch.setattr(srv, "_estimate", lambda *a: 0.20)
    ws.settings.set("mcp_daily_budget_usd", 0.5)
    with pytest.raises(ToolError, match="하루 상한"):
        srv.call_tool("generate", {"image_ids": ids[:3], "steps": ["front_view"]})              # 0.60 > 0.5
    ws.settings.set("mcp_daily_budget_usd", 0)
    with pytest.raises(ToolError, match="하루 상한"):
        srv.call_tool("generate", {"image_ids": ids[:1], "steps": ["front_view"]})              # 0 = 금지

    ws.settings.set("mcp_daily_budget_usd", 0.5)
    out = srv.call_tool("generate", {"image_ids": ids[:2], "steps": ["front_view"], "wait_sec": 30})
    assert json.loads(out[1]["text"])["state"] == "done"
    rows = ws.db._all("SELECT source, status FROM usage_log", ())
    assert [(r["source"], r["status"]) for r in rows] == [("mcp", "ok"), ("mcp", "ok")]
    # 실제 사용액(요금표 계산)이 오늘 사용액에 반영된다
    assert srv._mcp_spent_today() > 0
    assert json.loads(srv.call_tool("status", {})[0]["text"])["mcp_spent_today_usd_estimate"] > 0

    ws.settings.set("monthly_budget_usd", 0.01)
    with pytest.raises(ToolError, match="월간 예산"):
        srv.call_tool("generate", {"image_ids": ids[:1], "steps": ["front_view"]})


def test_no_key_and_failure_recorded(ws, sample_png):
    from crs import keystore
    from crs.mcp_server import ToolError
    from crs.services.image_service import ImageGenerationService
    from conftest import error_response
    ws.settings.set("mcp_enabled", True)
    srv = _server(ws)
    pid = ws.create_project("P")
    iid = ws.import_original(pid, sample_png)
    with pytest.raises(ToolError, match="API 키"):
        srv.call_tool("generate", {"image_ids": [iid], "steps": ["front_view"]})
    keystore.set_key(FAKE_KEY)
    ws._real_service = ImageGenerationService(keystore.get_key, http_client=make_mock_client(
        lambda r: error_response(400, "moderation_blocked", "blocked by safety system")))
    out = srv.call_tool("generate", {"image_ids": [iid], "steps": ["front_view", "four_views"], "wait_sec": 30})
    info = json.loads(out[1]["text"])
    assert info["state"] == "failed" and len(info["errors"]) == 1 and info["done"] == 1   # 실패하면 다음 단계는 보내지 않음
    assert ws.db._one("SELECT source, status FROM usage_log", ())["source"] == "mcp"
    assert FAKE_KEY not in json.dumps(info, ensure_ascii=False)
    for f in ws.paths.logs.glob("*"):                       # 키는 로그에도 남지 않는다
        assert FAKE_KEY not in f.read_text(encoding="utf-8", errors="ignore")


# ------------------------------------------------------------------ 폰에서 받은 원본
def test_inbox_receive_and_generate_marks_done(tmp_path, monkeypatch):
    from fake_drive import FakeDrive
    from test_sync import engine, make_ws
    from crs.sync.drive import DriveClient
    drive = FakeDrive().start()
    try:
        pc = make_ws(tmp_path, "pc", monkeypatch)
        pc.settings.set("demo_mode", True)
        pc.settings.set("mcp_enabled", True)
        # 폰 웹앱이 올리는 것과 같은 형식으로 '받은편지함' 항목 2개 업로드
        client = DriveClient(lambda: "tok", api=drive.base + "/drive/v3", upload=drive.base + "/upload/drive/v3")
        folder = client.find_or_create_folder()
        import io as _io
        for i, (proj, note) in enumerate((("캐릭터B", "사면도까지 뽑아 줘"), ("", ""))):
            buf = _io.BytesIO()
            Image.new("RGB", (40, 50), (10 * i, 100, 200)).save(buf, "JPEG")
            client.upload(folder, f"inbox_{i}.png", buf.getvalue(), {
                "v": 1, "uid": f"inboxuid{i}", "kind": "inbox", "name": f"폰사진{i}", "note": note, "tags": "",
                "project": proj, "created": "2026-11-15T13:00:00Z", "updated": "2026-11-15T13:00:00Z"})
        st = engine(pc, drive).sync()
        assert st.inbox_received == 2 and "폰 원본 2" in st.summary() and not st.errors
        assert len(drive.items()) == 0                               # 받은 뒤 드라이브 사본은 휴지통
        assert st.uploaded == 0                                      # 원본은 다시 올리지 않는다
        b = pc.db.get_project_by_name("캐릭터B")
        assert b and pc.db.get_project_by_name("폰에서 받은 원본")
        assert engine(pc, drive).sync().inbox_received == 0         # 두 번 받지 않는다

        srv = _server(pc)
        inbox = json.loads(srv.call_tool("list_inbox", {})[0]["text"])
        assert inbox["count"] == 2
        item = next(x for x in inbox["items"] if x["project"] == "캐릭터B")
        assert item["note"] == "사면도까지 뽑아 줘" and Path(item["path"]).exists()
        out = srv.call_tool("generate", {"image_ids": [item["id"]], "steps": ["four_views"], "wait_sec": 30})
        assert json.loads(out[1]["text"])["state"] == "done"
        assert json.loads(srv.call_tool("list_inbox", {})[0]["text"])["count"] == 1
        srv.call_tool("mark_inbox_done", {"image_ids": [x["id"] for x in inbox["items"]]})
        assert "없습니다" in srv.call_tool("list_inbox", {})[0]["text"]
        pc.close()
    finally:
        drive.stop()


def test_sync_lock_blocks_second_sync(tmp_path):
    from crs.sync.lock import SyncBusy, sync_lock
    with sync_lock(tmp_path):
        with pytest.raises(SyncBusy):
            with sync_lock(tmp_path, wait_ms=100):
                pass
    with sync_lock(tmp_path, wait_ms=100):
        pass

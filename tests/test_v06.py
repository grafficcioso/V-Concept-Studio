"""v0.6.0: 헤드+헤어 세트(크기 맞추기), 헤드 사면도 기준 헤어, 겹쳐 보기, 핀터레스트 가져오기."""
from __future__ import annotations

import json

from PIL import Image

from conftest import error_response, ok_response
from test_ui import qapp, setup_project, use_mock_api, wait_idle, win  # noqa: F401  (픽스처 재사용)


def settle(qapp, win, timeout=20):
    """이어서 보내는 요청(세트 2/2·일괄 생성)까지 모두 끝날 때까지 기다린다."""
    import time
    end = time.time() + timeout
    quiet = 0
    while time.time() < end and quiet < 15:
        qapp.processEvents()
        busy = win.ctl.is_busy() or win.gen._batch or win.gen._pending_set
        quiet = 0 if busy else quiet + 1
        time.sleep(0.02)
    assert not (win.ctl.is_busy() or win.gen._batch or win.gen._pending_set)


def _parts_sheet(ws, image_id):
    """결과 이미지를 '헤어 왼쪽(갈색) + 헤드 오른쪽(살색)' 파트 시트로 바꾼다."""
    path = ws.image_path(ws.db.get_image(image_id))
    big = Image.new("RGB", (400, 300), "white")
    for x in range(30, 130):
        for y in range(40, 200):
            big.putpixel((x, y), (120, 60, 20))
    for x in range(250, 350):
        for y in range(60, 220):
            big.putpixel((x, y), (250, 210, 190))
    big.save(path)


def _colors(ws, image_id):
    im = Image.open(ws.image_path(ws.db.get_image(image_id))).convert("RGB")
    return {c for _, c in im.getcolors(100000)}


def test_part_views_prompt_reference_order():
    from crs.ui.retouch_dialog import build_part_views_prompt
    t = "Hair. {reference_instruction}\n\n### Negative\nface"
    p = build_part_views_prompt(t, True, head_sheet=True)
    assert "SECOND image is the four-view sheet of the bald head" in p and "THIRD image is the original" in p
    p = build_part_views_prompt(t, True)
    assert "SECOND image is the original" in p and "bald head" not in p
    # 사용자가 자리표시자를 지운 프롬프트에도 안내가 붙고, Negative 는 그대로 아래에 남는다
    p = build_part_views_prompt("Hair only.\n\n### Negative\nface", False, head_sheet=True)
    assert p.index("bald head") < p.index("### Negative")


def test_head_hair_set_chains_two_requests(qapp, win, ws, sample_png, monkeypatch):
    from PySide6.QtCore import QPointF
    from crs.ui import retouch_dialog
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    pid = setup_project(win, ws, sample_png)
    win.gen.generate()
    wait_idle(qapp, win)
    sheet_id = win.gen.result_id
    _parts_sheet(ws, sheet_id)
    before = len(seen)

    def fake_exec(self):
        self.part_type.setCurrentIndex(self.part_type.findData(retouch_dialog.SET_KEY))
        assert self.layer_box.isVisibleTo(self)
        self.canvas.brush = 60
        self.canvas._stroke(QPointF(300, 80), QPointF(300, 200))   # 헤드 (빨강)
        self.layer_hair.setChecked(True)
        assert self.canvas.ghost is not None                       # 헤드는 파랑으로 보임
        self.canvas._stroke(QPointF(80, 60), QPointF(80, 180))     # 헤어
        self.use_original.setChecked(True)
        return 1

    monkeypatch.setattr(retouch_dialog.PartViewsDialog, "exec", fake_exec)
    win.gen.part_views(sheet_id)
    settle(qapp, win)
    assert len(seen) - before == 2
    outs = {o["function"]: o for o in ws.db.list_images(pid, "output")}
    head, hair = outs["part_views_head"], outs["part_views_hair"]
    assert head["id"] < hair["id"]
    assert (250, 210, 190) in _colors(ws, head["source_image_id"]) and \
        (120, 60, 20) not in _colors(ws, head["source_image_id"])
    assert (120, 60, 20) in _colors(ws, hair["source_image_id"]) and \
        (250, 210, 190) not in _colors(ws, hair["source_image_id"])
    body = seen[-1].content
    assert body.count(b'name="image') == 3 and b"bald head that this wig must fit" in body
    params = json.loads(hair["params_json"])
    assert params["head_sheet_id"] == head["id"] and params["set_step"] == 2
    assert win.gen.result_id == hair["id"]
    # 정렬 가이드
    shown = []
    monkeypatch.setattr(retouch_dialog.OverlayDialog, "exec", lambda self: shown.append(self.composite().size))
    win.gen.overlay_with_head()
    assert shown


def test_head_failure_does_not_send_hair(qapp, win, ws, sample_png, monkeypatch):
    from PySide6.QtCore import QPointF
    from crs.ui import retouch_dialog
    seen = []
    pid = setup_project(win, ws, sample_png)
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    win.gen.generate()
    wait_idle(qapp, win)
    sheet_id = win.gen.result_id
    _parts_sheet(ws, sheet_id)
    use_mock_api(ws, lambda request: (seen.append(request), error_response(400, "invalid_value", "bad"))[1])
    n = len(seen)

    def fake_exec(self):
        self.part_type.setCurrentIndex(self.part_type.findData(retouch_dialog.SET_KEY))
        self.canvas.brush = 60
        self.canvas._stroke(QPointF(300, 80), QPointF(300, 200))
        self.layer_hair.setChecked(True)
        self.canvas._stroke(QPointF(80, 60), QPointF(80, 180))
        return 1

    monkeypatch.setattr(retouch_dialog.PartViewsDialog, "exec", fake_exec)
    win.gen.part_views(sheet_id)
    settle(qapp, win)
    assert len(seen) - n == 1 and any("헤어는 보내지 않았습니다" in m for m in win.messages)
    assert [r for r in ws.db.list_images(pid, "part_input")] == [] or \
        all("헤어" not in (r["name"] or "") for r in ws.db.list_images(pid, "part_input"))


def test_set_requires_both_parts(qapp, win, ws, sample_png):
    from PySide6.QtCore import QPointF
    from crs.ui.retouch_dialog import SET_KEY, PartViewsDialog
    dlg = PartViewsDialog(sample_png, has_original=False)
    dlg.part_type.setCurrentIndex(dlg.part_type.findData(SET_KEY))
    dlg.canvas.brush = 10
    dlg.canvas._stroke(QPointF(5, 5), QPointF(20, 20))
    assert dlg._has(dlg._layer_image("head")) and not dlg._has(dlg._layer_image("hair"))
    st = dlg.state()
    dlg2 = PartViewsDialog(sample_png, has_original=False, state=st)   # 다시 열면 세트·칠한 것 복원
    assert dlg2.is_set() and dlg2._has(dlg2._layer_image("head")) and not dlg2._has(dlg2._layer_image("hair"))


def test_hair_alone_can_use_existing_head_sheet(qapp, win, ws, sample_png, monkeypatch):
    from PySide6.QtCore import QPointF
    from crs.ui import retouch_dialog
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    pid = setup_project(win, ws, sample_png)
    win.gen.generate()
    wait_idle(qapp, win)
    sheet_id = win.gen.result_id
    _parts_sheet(ws, sheet_id)

    def head_exec(self):
        self.part_type.setCurrentIndex(self.part_type.findData("part_views_head"))
        self.canvas.brush = 60
        self.canvas._stroke(QPointF(300, 80), QPointF(300, 200))
        return 1

    monkeypatch.setattr(retouch_dialog.PartViewsDialog, "exec", head_exec)
    win.gen.part_views(sheet_id)
    wait_idle(qapp, win)
    head_id = win.gen.result_id
    picked = {}

    def hair_exec(self):
        self.part_type.setCurrentIndex(self.part_type.findData("part_views_hair"))
        picked["default"] = self.head_sheet.currentData()
        picked["visible"] = self.head_box.isVisibleTo(self)
        self.canvas.clear()
        self.canvas._stroke(QPointF(80, 60), QPointF(80, 180))
        return 1

    monkeypatch.setattr(retouch_dialog.PartViewsDialog, "exec", hair_exec)
    win.gen.part_views(sheet_id)
    wait_idle(qapp, win)
    assert picked == {"default": head_id, "visible": True}
    hair = ws.db.get_image(win.gen.result_id)
    assert hair["function"] == "part_views_hair" and json.loads(hair["params_json"])["head_sheet_id"] == head_id
    assert b"bald head that this wig must fit" in seen[-1].content


# ------------------------------------------------------------------ 핀터레스트 (네트워크는 가짜)
RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>b</title>
<item><title>Pin A  hair</title><link>https://www.pinterest.com/pin/111/</link>
<description>&lt;a href="https://www.pinterest.com/pin/111/"&gt;&lt;img src="https://i.pinimg.com/236x/aa/bb/cc/a.jpg"&gt;&lt;/a&gt;A</description>
<pubDate>Sat, 21 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Pin B</title><link>https://www.pinterest.com/pin/222/</link>
<description>&lt;img src="https://i.pinimg.com/236x/dd/ee/ff/b.jpg"&gt;</description>
<pubDate>Sun, 01 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>no image</title><link>https://www.pinterest.com/pin/333/</link><description>text</description></item>
</channel></rss>"""


def _jpeg(color=(200, 80, 120), size=(80, 100)):
    import io
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


class FakeNet:
    def __init__(self, originals_ok=True):
        self.urls = []
        self.originals_ok = originals_ok

    def __call__(self, url, limit=None):
        self.urls.append(url)
        if url.endswith(".rss"):
            if "missing" in url:
                import urllib.error
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            return RSS.encode()
        if "/originals/" in url and not self.originals_ok:
            raise OSError("403")
        return _jpeg(size=(800, 1000) if "/originals/" in url else (236, 300))


def test_pinterest_url_and_parse():
    import pytest
    from crs import pinterest as pt
    assert pt.board_rss_url("https://kr.pinterest.com/kataya/hair-ref/") == "https://www.pinterest.com/kataya/hair-ref.rss"
    assert pt.board_rss_url("kataya/hair-ref") == "https://www.pinterest.com/kataya/hair-ref.rss"
    assert pt.board_rss_url("https://www.pinterest.com/kataya/") == "https://www.pinterest.com/kataya/feed.rss"
    assert pt.board_key("https://www.pinterest.co.kr/kataya/hair-ref/") == "kataya/hair-ref"
    for bad in ("", "https://example.com/a/b", "https://pin.it/abc", "https://www.pinterest.com/pin/123/"):
        with pytest.raises(pt.PinterestError):
            pt.board_rss_url(bad)
    pins = pt.parse_rss(RSS.encode())
    assert [p.pin_id for p in pins] == ["111", "222"] and pins[0].title == "Pin A hair"
    assert pt.image_urls(pins[0].thumb_url)[:2] == ["https://i.pinimg.com/originals/aa/bb/cc/a.jpg",
                                                    "https://i.pinimg.com/736x/aa/bb/cc/a.jpg"]
    assert [p.pin_id for p in pt.filter_since(pins, pt.parse_since("2026-11-10"))] == ["111"]
    with pytest.raises(pt.PinterestError, match="찾지 못했습니다"):
        pt.fetch_board("someone/missing", get=FakeNet())
    with pytest.raises(pt.PinterestError, match="RSS"):
        pt.parse_rss(b"<html>login</html")
    net = FakeNet(originals_ok=False)       # 원본 크기가 막히면 736px 로
    data, url = pt.download_image(pins[0], get=net)
    assert "/736x/" in url and Image.open(__import__("io").BytesIO(data)).size == (236, 300)


def test_pinterest_mcp_preview_then_confirm(ws):
    import pytest
    from crs.mcp_server import Server, ToolError
    ws.settings.set("mcp_enabled", True)
    ws.settings.set("demo_mode", True)
    srv = Server(ws_factory=lambda: ws)
    net = FakeNet()
    srv.pinterest_get = net
    lst = json.loads(srv.call_tool("pinterest_list", {"board": "kataya/hair-ref"})[0]["text"])
    assert lst["count"] == 2 and all(p["imported_image_id"] is None for p in lst["pins"])
    args = {"board": "kataya/hair-ref", "since": "2026-11-10", "steps": ["front_view"]}
    pre = json.loads(srv.call_tool("pinterest_import", args)[0]["text"])
    assert pre["new_pins"] == 1 and pre["generate_images"] == 1 and "confirm=true" in pre["hint"]
    assert ws.db.pinterest_seen() == {} and not any("/originals/" in u for u in net.urls)   # 미리보기는 아무것도 안 받음
    out = srv.call_tool("pinterest_import", {**args, "confirm": True, "wait_sec": 30})
    info = json.loads(out[0]["text"])
    assert len(info["imported_ids"]) == 1 and "외부 출처" in info["note"]
    row = ws.db.get_image(info["imported_ids"][0])
    assert "외부 출처" in row["tags"] and "판매·커미션·게시 금지" in row["note"] and "pin/111" in row["note"]
    assert Image.open(ws.image_path(row)).size == (800, 1000)            # 원본 크기
    assert ws.db.pinterest_seen() == {"111": row["id"]}
    proj = ws.db.get_project(row["project_id"])
    assert proj["name"] == "핀터레스트"
    outs = ws.db.list_images(row["project_id"], "output")
    assert len(outs) == 1 and outs[0]["function"] == "front_view" and outs[0]["source_image_id"] == row["id"]
    again = json.loads(srv.call_tool("pinterest_import", {**args, "confirm": True})[0]["text"])
    assert again["imported_ids"] == [] and again["already_imported"] == ["111"]   # 중복 방지
    with pytest.raises(ToolError, match="pin_ids 또는 since"):
        srv.call_tool("pinterest_import", {"board": "kataya/hair-ref"})
    ws.settings.set("mcp_max_images_per_call", 1)
    with pytest.raises(ToolError, match="최대 장수"):
        srv.call_tool("pinterest_import", {"board": "kataya/hair-ref", "pin_ids": ["222"],
                                           "steps": ["front_view", "four_views"], "confirm": True})
    assert "222" not in ws.db.pinterest_seen()      # 거절되면 가져오지도 않는다


def test_pinterest_dialog_import_and_batch(qapp, win, ws, monkeypatch):
    from PySide6.QtCore import Qt
    from crs.ui import pinterest_ui
    ws.settings.set("demo_mode", True)
    ws._demo_service.delay = 0
    net = FakeNet()
    orig_init = pinterest_ui.PinterestDialog.__init__

    def init(self, *a, **k):
        k["get"] = net
        orig_init(self, *a, **k)
    monkeypatch.setattr(pinterest_ui.PinterestDialog, "__init__", init)

    def fake_exec(self):
        self.board.setCurrentText("https://www.pinterest.com/kataya/hair-ref/")
        self.save_board()
        self.load()
        self._threads[-1].wait(5000)
        for _ in range(20):
            qapp.processEvents()
        assert self.list.count() == 2
        self.since_on.setChecked(True)
        from PySide6.QtCore import QDate
        self.since.setDate(QDate(2026, 11, 10))
        self.select_matching()
        assert [p.pin_id for p in self.checked_pins()] == ["111"]
        self.step.setCurrentIndex(3)                 # 정면화 → 사면도
        assert "생성 2장" in self.summary.text()
        self.do_import()
        self._threads[-1].wait(5000)
        for _ in range(20):
            qapp.processEvents()
        return self.result()

    monkeypatch.setattr(pinterest_ui.PinterestDialog, "exec", fake_exec)
    win.gen.open_pinterest()
    settle(qapp, win)
    assert ws.settings.get("pinterest_boards") == ["https://www.pinterest.com/kataya/hair-ref/"]
    p = ws.db.get_project_by_name("핀터레스트")
    origs = ws.db.list_images(p["id"], "original")
    outs = ws.db.list_images(p["id"], "output")
    assert len(origs) == 1 and "외부 출처" in origs[0]["tags"]
    assert [o["function"] for o in reversed(outs)] == ["front_view", "four_views"]
    assert outs[0]["source_image_id"] == outs[1]["id"]          # 사면도는 정면화 결과로
    assert "일괄 생성 완료 (2/2" in win.gen.progress_label.text()

"""웹앱 브라우저 테스트: 헤드리스 크롬, 폰 화면 크기, OpenAI 응답은 모의(네트워크·비용 없음)."""
import base64
import functools
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from email.parser import BytesParser
from email.policy import default as email_default
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SHOTS = Path(os.environ.get("WEB_SHOTS", "/tmp/web_shots"))


def png_b64(color=(0, 200, 0, 255), size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def parse_multipart(req):
    raw = b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.post_data_buffer
    msg = BytesParser(policy=email_default).parsebytes(raw)
    parts = []
    for p in msg.iter_parts():
        parts.append((p.get_param("name", header="content-disposition"), p.get_filename(), p.get_payload(decode=True)))
    return parts


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_web.py"), str(out / "s")], check=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out / "s"))
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        exe = shutil.which("google-chrome") or shutil.which("chromium")
        b = p.chromium.launch(executable_path=exe, headless=True) if exe else p.chromium.launch(channel="chrome")
        yield b
        b.close()


class Api:
    def __init__(self):
        self.requests = []
        self.mode = "ok"

    def handle(self, route, request):
        cors = {"access-control-allow-origin": "*", "access-control-expose-headers": "X-Request-ID"}
        if request.method == "OPTIONS":
            return route.fulfill(status=200, headers={**cors, "access-control-allow-headers": "authorization,content-type",
                                                      "access-control-allow-methods": "POST, GET"})
        self.requests.append(request)
        if self.mode == "auth":
            return route.fulfill(status=401, headers={**cors, "content-type": "application/json", "x-request-id": "req_err"},
                                 body=json.dumps({"error": {"message": "Incorrect API key", "code": "invalid_api_key"}}))
        if request.url.endswith("/models"):
            return route.fulfill(status=200, headers={**cors, "content-type": "application/json"},
                                 body=json.dumps({"data": [{"id": "gpt-image-2.6-flare"}, {"id": "gpt-6"}]}))
        body = {"created": 1, "data": [{"b64_json": png_b64()}], "output_format": "png", "size": "1024x1024",
                "usage": {"input_tokens": 1100, "input_tokens_details": {"image_tokens": 1000, "text_tokens": 100},
                          "output_tokens": 800, "total_tokens": 1900}}
        return route.fulfill(status=200, headers={**cors, "content-type": "application/json", "x-request-id": "req_web_1"},
                             body=json.dumps(body))


@pytest.fixture
def page(browser, site):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    api = Api()
    pg.route("https://api.openai.com/**", api.handle)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(site)
    pg.wait_for_selector("#fnChips .chip")
    pg.api, pg.errors = api, errors
    yield pg
    ctx.close()
    assert not errors, errors


def set_key(pg, key="sk-test-WEB1234567890"):
    pg.click(".tabbar [data-go=settings]")
    pg.fill("#keyInput", key)
    pg.click("#keySave")
    pg.click(".tabbar [data-go=create]")


def pick_image(pg, tmp_path, transparent=True):
    p = tmp_path / "in.png"
    im = Image.new("RGBA", (200, 160), (0, 0, 0, 0))      # 투명 + 숨은 검정
    for x in range(60, 140):
        for y in range(40, 120):
            im.putpixel((x, y), (255, 120, 170, 255))
    if not transparent:
        im = im.convert("RGB")
    im.save(p)
    pg.set_input_files("#fileInput", str(p))
    pg.wait_for_selector("#inputImg:not([hidden])")


def confirm(pg):
    pg.wait_for_selector("#modal:not([hidden])")
    pg.click("#modalBtns button.primary")


def test_generate_parts_sheet_real_request_shape(page, tmp_path):
    pg = page
    set_key(pg)
    assert "등록됨" in pg.text_content("#apiStatus")
    pick_image(pg, tmp_path)
    pg.click("#fnChips [data-fn=parts_separation]")
    pg.click("#genBtn")
    confirm(pg)
    pg.wait_for_selector("#resultCard:not([hidden])")
    req = pg.api.requests[-1]
    assert req.url.endswith("/images/edits") and req.headers["authorization"] == "Bearer sk-test-WEB1234567890"
    parts = parse_multipart(req)
    names = [n for n, _, _ in parts]
    fields = {n: v.decode() for n, f, v in parts if not f}
    assert fields["model"] and "character-parts separation sheet" in fields["prompt"] and fields["n"] == "1"
    assert "quality" not in fields and "size" not in fields   # auto 는 보내지 않음
    imgs = [v for n, f, v in parts if n == "image[]"]
    assert len(imgs) == 1 and names.count("markup") == 0
    sent = Image.open(io.BytesIO(imgs[0])).convert("RGBA")
    assert sent.getpixel((5, 5)) == (255, 255, 255, 255)        # 투명 → 흰색 (검정 아님)
    assert sent.getpixel((100, 80))[:3] == (255, 120, 170)
    meta = pg.text_content("#resultMeta")
    assert "파트 분할" in meta and "$" in meta
    SHOTS.mkdir(parents=True, exist_ok=True)
    pg.screenshot(path=str(SHOTS / "create.png"), full_page=True)
    # 기록
    pg.click(".tabbar [data-go=history]")
    pg.wait_for_selector("#histGrid .thumb")
    assert pg.locator("#histGrid .thumb").count() == 1
    pg.click("#histFilter [data-f=original]")
    assert pg.locator("#histGrid .thumb").count() == 1
    pg.screenshot(path=str(SHOTS / "history.png"))


def test_retouch_with_markup_and_reference(page, tmp_path):
    pg = page
    set_key(pg)
    pick_image(pg, tmp_path)
    pg.click("#genBtn")
    confirm(pg)
    pg.wait_for_selector("#resultCard:not([hidden])")
    pg.click("#resultCard [data-act=retouch]")
    pg.wait_for_selector("#retouch:not([hidden])")
    box = pg.locator("#rtCanvas").bounding_box()
    pg.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.3)
    pg.mouse.down()
    pg.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.6, steps=8)
    pg.mouse.up()
    pg.click("#rtQuick .chip >> nth=0")
    pg.fill("#rtRequest", pg.input_value("#rtRequest") + "\n머리카락 줄여줘")
    assert pg.is_checked("#rtUseOrig")
    pg.screenshot(path=str(SHOTS / "retouch.png"))
    pg.click("#rtSend")
    confirm(pg)
    pg.wait_for_function("document.querySelector('#resultMeta').textContent.includes('부분 수정')")
    parts = parse_multipart(pg.api.requests[-1])
    imgs = [v for n, f, v in parts if n == "image[]"]
    markup = [v for n, f, v in parts if n == "markup"]
    prompt = {n: v.decode() for n, f, v in parts if not f}["prompt"]
    assert len(imgs) == 2 and len(markup) == 1
    first = Image.open(io.BytesIO(imgs[0]))
    mk = Image.open(io.BytesIO(markup[0])).convert("RGBA")
    assert mk.size == first.size                                   # 주석 레이어 = 첫 이미지 크기
    alphas = [mk.getpixel((x, y))[3] for x in range(mk.width) for y in range(mk.height)]
    assert max(alphas) > 0 and min(alphas) == 0
    assert "머리카락 줄여줘" in prompt and "red-marked area" in prompt and "SECOND image" in prompt and "{" not in prompt


def test_auth_error_and_no_retry(page, tmp_path):
    pg = page
    set_key(pg, "sk-wrong-000000000000")
    pick_image(pg, tmp_path)
    pg.api.mode = "auth"
    pg.click("#genBtn")
    confirm(pg)
    pg.wait_for_selector("#modal:not([hidden])")
    assert "API 키가 올바르지" in pg.text_content("#modalText")
    assert len(pg.api.requests) == 1
    pg.click("#modalBtns button")
    assert pg.is_enabled("#genBtn")


def test_no_key_and_demo_mode(page, tmp_path):
    pg = page
    pick_image(pg, tmp_path, transparent=False)
    pg.click("#genBtn")
    pg.wait_for_selector("#modal:not([hidden])")
    assert "API 키 필요" in pg.text_content("#modalTitle")
    pg.click("#modalBtns button")
    pg.check("#demoMode")
    assert "데모" in pg.text_content("#apiStatus")
    pg.click(".tabbar [data-go=create]")
    pg.click("#genBtn")
    pg.wait_for_selector("#resultCard:not([hidden])")
    assert pg.api.requests == []


def test_key_test_and_settings_persist(page, tmp_path):
    pg = page
    set_key(pg)
    pg.click(".tabbar [data-go=settings]")
    pg.click("#keyTest")
    pg.wait_for_function("document.querySelector('#keyMsg').textContent.includes('연결 성공')")
    assert "gpt-image-2.6-flare" in pg.text_content("#keyMsg") and "gpt-6," not in pg.text_content("#keyMsg")
    pg.fill("#creditInput", "5")
    pg.fill("#creditSince", "2026-01-01")
    pg.click("#moneySave")
    pg.select_option("#promptFn", "front_view")
    assert "head tilt" in pg.input_value("#promptNeg") and "### Negative" not in pg.input_value("#promptText")
    pg.fill("#promptText", "MY FRONT PROMPT")
    pg.fill("#promptNeg", "blur, text")
    pg.click("#promptSave")
    pg.screenshot(path=str(SHOTS / "settings.png"), full_page=True)
    pg.reload()
    pg.wait_for_selector("#fnChips .chip")
    pick_image(pg, tmp_path)
    pg.click("#genBtn")
    confirm(pg)
    pg.wait_for_selector("#resultCard:not([hidden])")
    fields = {n: v.decode() for n, f, v in parse_multipart(pg.api.requests[-1]) if not f}
    assert fields["prompt"].replace("\r\n", "\n") == ("MY FRONT PROMPT\n\nAvoid (negative prompt). The result must NOT contain or show "
                                "any of the following: blur, text")
    pg.click(".tabbar [data-go=settings]")
    pg.wait_for_function("document.querySelector('#usageSummary').textContent.includes('추정 잔액')")
    assert "추정 잔액 $4.9" in pg.text_content("#usageSummary")


# ============================================================ 드라이브 동기화 (PC ↔ 드라이브 ↔ 웹)
GIS_MOCK = """
window.google = { accounts: { oauth2: {
  initTokenClient: (cfg) => ({ requestAccessToken: () => setTimeout(() => cfg.callback({ access_token: 'tok', expires_in: 3600 }), 20) }),
  revoke: () => {} } } };
"""


def _pc(tmp_path, monkeypatch, name):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    from crs.config import Settings
    from crs.workspace import Workspace
    monkeypatch.setenv("CRS_CONFIG_DIR", str(tmp_path / f"cfg_{name}"))
    s = Settings()
    s.set("data_root", str(tmp_path / f"data_{name}"))
    return Workspace(s)


def _pc_sync(ws, drive):
    from crs.sync.drive import DriveClient
    from crs.sync.engine import SyncEngine
    return SyncEngine(ws, DriveClient(lambda: "tok", api=drive.base + "/drive/v3", upload=drive.base + "/upload/drive/v3")).sync()


def _wait_text(pg, sel, text, timeout=15000):
    pg.wait_for_function(f"document.querySelector({json.dumps(sel)}).textContent.includes({json.dumps(text)})", timeout=timeout)


def test_drive_sync_pc_web_roundtrip(browser, site, tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tests"))
    from fake_drive import FakeDrive
    drive = FakeDrive().start()
    try:
        # PC: 결과 1 + 에셋 1 을 올림
        pc = _pc(tmp_path, monkeypatch, "pc")
        pid = pc.create_project("캐릭터A")
        buf = io.BytesIO()
        Image.new("RGBA", (40, 30), (255, 0, 0, 255)).save(buf, "PNG")
        out_id = pc.save_output(pid, buf.getvalue(), function="front_view", source_image_id=None, prompt="p", preset_id=None,
                                preset_name="", model="gpt-image-2.6-sunburst", quality="low", size="auto", background="auto",
                                params={}, request_id="r", usage=None, est_cost_usd=None)
        pc.db.update_image(out_id, name="PC 정면")
        asset_id = pc.register_asset(Image.new("RGBA", (20, 20), (0, 255, 0, 255)), "Hair", "트윈테일", "핑크")
        assert _pc_sync(pc, drive).uploaded == 2

        # 웹: 연결 → 받기
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        api = Api()
        pg.route("https://api.openai.com/**", api.handle)
        pg.route("https://accounts.google.com/**", lambda r, q: r.fulfill(status=200, body="", headers={"content-type": "text/javascript"}))
        pg.add_init_script(GIS_MOCK + f"window.__VC_DRIVE_BASE = {json.dumps(drive.base)};")
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(site)
        pg.wait_for_selector("#fnChips .chip")
        pg.click(".tabbar [data-go=settings]")
        pg.fill("#gClientId", "web.apps.googleusercontent.com")
        pg.click("#gConnect")
        _wait_text(pg, "#gStatus", "받음 2")
        pg.click(".tabbar [data-go=history]")
        pg.wait_for_selector("#histGrid .thumb")
        assert pg.locator("#histGrid .thumb").count() == 1 and "PC 정면" in pg.text_content("#histGrid")
        pg.click("#histFilter [data-f=asset]")
        _wait_text(pg, "#histGrid", "트윈테일")
        assert pg.locator("#histGrid .thumb").count() == 1
        pg.screenshot(path=str(SHOTS / "sync_history.png"))

        # 웹에서 즐겨찾기 → 자동 동기화로 드라이브에 반영 → PC 가 받음
        pg.click("#histFilter [data-f=output]")
        _wait_text(pg, "#histGrid", "PC 정면")
        pg.click("#histGrid .thumb >> nth=0")
        pg.click("#detail [data-dact=fav]")
        pg.click("#detail [data-close]")
        pg.wait_for_function("document.querySelector('#gStatus').textContent.includes('정보 갱신 1')", timeout=15000)
        assert _pc_sync(pc, drive).meta_pulled == 1 and pc.db.get_image(out_id)["favorite"] == 1

        # 웹에서 새로 생성 → 자동 업로드 → PC 가 '웹앱' 프로젝트로 받음
        pg.click(".tabbar [data-go=settings]")
        pg.fill("#keyInput", "sk-test-WEB1234567890")
        pg.click("#keySave")
        pg.click(".tabbar [data-go=create]")
        p = tmp_path / "w.png"
        Image.new("RGB", (64, 64), "white").save(p)
        pg.set_input_files("#fileInput", str(p))
        pg.wait_for_selector("#inputImg:not([hidden])")
        pg.click("#genBtn")
        pg.wait_for_selector("#modal:not([hidden])")
        pg.click("#modalBtns button.primary")
        pg.wait_for_selector("#resultCard:not([hidden])")
        pg.wait_for_function("Object.keys(window).length && true")
        deadline = __import__("time").time() + 15
        while len(drive.items()) < 3 and __import__("time").time() < deadline:
            pg.wait_for_timeout(200)
        assert len(drive.items()) == 3
        st = _pc_sync(pc, drive)
        assert st.downloaded == 1 and pc.db.get_project_by_name("웹앱") is not None

        # 웹에서 에셋 삭제 → 드라이브 휴지통 → PC 에서도 삭제
        pg.click(".tabbar [data-go=history]")
        pg.click("#histFilter [data-f=asset]")
        _wait_text(pg, "#histGrid", "트윈테일")
        pg.click("#histGrid .thumb >> nth=0")
        pg.click("#detail [data-dact=delete]")
        pg.wait_for_selector("#modal:not([hidden])")
        pg.click("#modalBtns button.danger")
        while len(drive.items()) > 2 and __import__("time").time() < deadline + 15:
            pg.wait_for_timeout(200)
        assert len(drive.items()) == 2
        assert _pc_sync(pc, drive).deleted_local == 1 and pc.db.get_asset(asset_id) is None
        assert not errors, errors
        ctx.close()
        pc.close()
    finally:
        drive.stop()


def test_send_originals_to_pc(browser, site, tmp_path, monkeypatch):
    """폰 웹앱 [PC로 보내기] → 드라이브 → PC 가 '받은 원본'으로 받고 드라이브 사본은 휴지통."""
    sys.path.insert(0, str(ROOT / "tests"))
    from fake_drive import FakeDrive
    drive = FakeDrive().start()
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.route("https://accounts.google.com/**", lambda r, q: r.fulfill(status=200, body="", headers={"content-type": "text/javascript"}))
        pg.add_init_script(GIS_MOCK + f"window.__VC_DRIVE_BASE = {json.dumps(drive.base)};")
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(site)
        pg.wait_for_selector("#fnChips .chip")
        files = []
        for i, color in enumerate(("red", "blue", "green")):
            p = tmp_path / f"phone_{i}.jpg"
            Image.new("RGB", (60, 80), color).save(p, "JPEG")
            files.append(str(p))
        pg.click("#pcCard summary")
        pg.set_input_files("#pcFiles", files)
        _wait_text(pg, "#pcPicked", "3장 선택됨")
        pg.fill("#pcProject", "캐릭터C")
        pg.fill("#pcNote", "사면도까지 뽑아 둬")
        pg.screenshot(path=str(SHOTS / "send_to_pc.png"), full_page=True)
        pg.click("#pcSend")
        _wait_text(pg, "#pcStatus", "지금까지 보낸 원본 3장")
        items = drive.items()
        assert len(items) == 3
        metas = [json.loads(f["description"]) for f in (items.values() if isinstance(items, dict) else items)]
        assert {m["kind"] for m in metas} == {"inbox"} and {m["note"] for m in metas} == {"사면도까지 뽑아 둬"}

        pc = _pc(tmp_path, monkeypatch, "pc2")
        st = _pc_sync(pc, drive)
        assert st.inbox_received == 3 and len(drive.items()) == 0
        proj = pc.db.get_project_by_name("캐릭터C")
        rows = pc.db._all("SELECT * FROM images WHERE inbox='new' AND project_id=?", (proj["id"],))
        assert len(rows) == 3 and {r["note"] for r in rows} == {"사면도까지 뽑아 둬"}
        assert sorted(r["name"] for r in rows) == ["phone_0", "phone_1", "phone_2"]

        # 웹은 다시 보내지 않고, 웹 기록의 원본도 지워지지 않는다
        pg.click("#syncStatus")
        _wait_text(pg, "#syncStatus", "연결됨")
        pg.wait_for_timeout(1500)
        assert len(drive.items()) == 0
        pg.click(".tabbar [data-go=history]")
        pg.click("#histFilter [data-f=original]")
        _wait_text(pg, "#histGrid", "phone_2")
        assert pg.locator("#histGrid .thumb").count() == 3
        assert not errors, errors
        ctx.close()
        pc.close()
    finally:
        drive.stop()

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
    pg.fill("#promptText", "MY FRONT PROMPT")
    pg.click("#promptSave")
    pg.screenshot(path=str(SHOTS / "settings.png"), full_page=True)
    pg.reload()
    pg.wait_for_selector("#fnChips .chip")
    pick_image(pg, tmp_path)
    pg.click("#genBtn")
    confirm(pg)
    pg.wait_for_selector("#resultCard:not([hidden])")
    fields = {n: v.decode() for n, f, v in parse_multipart(pg.api.requests[-1]) if not f}
    assert fields["prompt"] == "MY FRONT PROMPT"
    pg.click(".tabbar [data-go=settings]")
    pg.wait_for_function("document.querySelector('#usageSummary').textContent.includes('추정 잔액')")
    assert "추정 잔액 $4.9" in pg.text_content("#usageSummary")

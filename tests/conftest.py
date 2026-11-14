import io
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_KEY = "sk-test-THISISASECRETKEY1234567890"


def png_bytes(size=(64, 64), color=(200, 30, 30, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CRS_CONFIG_DIR", str(tmp_path / "cfg"))
    from crs import keystore
    from crs.config import Settings
    from crs.workspace import Workspace
    keystore._session_key = None
    s = Settings()
    s.set("data_root", str(tmp_path / "data"))
    w = Workspace(s)
    yield w
    try:
        w.close()
    except Exception:
        pass
    keystore._session_key = None


@pytest.fixture
def sample_png(tmp_path):
    p = tmp_path / "sample.png"
    im = Image.new("RGB", (300, 400), "white")
    for x in range(100, 200):
        for y in range(120, 280):
            im.putpixel((x, y), (30, 60, 200))
    im.save(p)
    return p


def make_mock_client(handler):
    """실제 OpenAI SDK 요청 경로를 타되 네트워크 대신 handler 가 응답하는 HTTP 클라이언트."""
    import httpx2
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def ok_response(usage=True):
    import base64
    import httpx2
    body = {"created": 1, "data": [{"b64_json": base64.b64encode(png_bytes((32, 32), (0, 200, 0, 255))).decode()}],
            "size": "1024x1024", "quality": "low", "output_format": "png", "background": "opaque"}
    if usage:
        body["usage"] = {"input_tokens": 1100, "input_tokens_details": {"image_tokens": 1000, "text_tokens": 100},
                         "output_tokens": 800, "total_tokens": 1900}
    return httpx2.Response(200, json=body, headers={"x-request-id": "req_test_123"})


def error_response(status, code, message, param=None, type_="invalid_request_error"):
    import httpx2
    return httpx2.Response(status, json={"error": {"message": message, "type": type_, "param": param, "code": code}},
                           headers={"x-request-id": "req_err_1"})


def responses_ok(prompt_en="clean thin line art, soft cel shading", summary="- 얇은 선화"):
    import httpx2
    body = {"id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-6-luna", "status": "completed",
            "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "annotations": [],
                                     "text": json.dumps({"prompt_en": prompt_en, "summary_ko": summary})}]}],
            "usage": {"input_tokens": 1500, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 900,
                      "output_tokens_details": {"reasoning_tokens": 600}, "total_tokens": 2400},
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": []}
    return httpx2.Response(200, json=body, headers={"x-request-id": "req_style_1"})


# Windows 에서는 세션이 끝난 뒤 파이썬이 Qt 객체를 정리하는 순서 때문에 종료 코드가 1로 바뀌는 경우가 있다.
# 모든 결과가 출력된 뒤(unconfigure) 실제 테스트 결과 코드로 즉시 종료한다.
_EXIT_STATUS = {"code": 0}


def pytest_sessionfinish(session, exitstatus):
    _EXIT_STATUS["code"] = int(exitstatus)


def pytest_unconfigure(config):
    if sys.platform == "win32":
        from crs.hard_exit import hard_exit
        hard_exit(_EXIT_STATUS["code"])

import json

import pytest
from PIL import Image

from conftest import FAKE_KEY, error_response, make_mock_client, ok_response, png_bytes


def test_workspace_bootstrap(ws):
    for name in ("front_view", "parts_separation", "four_views", "iris_restore", "upscale", "ai_compose"):
        assert (ws.paths.prompts / f"{name}.md").read_text(encoding="utf-8").strip()
    assert ws.paths.models_file.exists() and ws.paths.db_file.exists()
    assert "perfect straight-on front view" in ws.default_prompt("front_view")
    assert ws.catalog.model_ids()


def test_project_import_and_persistence(ws, sample_png, tmp_path):
    from crs.config import Settings
    from crs.workspace import Workspace
    pid = ws.create_project("테스트 캐릭터")
    with pytest.raises(ValueError):
        ws.create_project("테스트 캐릭터")
    oid = ws.import_original(pid, sample_png)
    jpg = tmp_path / "a.jpg"
    Image.new("RGB", (50, 50), "red").save(jpg)
    ws.import_original(pid, jpg)
    with pytest.raises(ValueError):
        ws.import_original(pid, tmp_path / "x.gif")
    assert sample_png.exists()  # 원본 미변경
    out = ws.save_output(pid, png_bytes(), function="front_view", source_image_id=oid, prompt="p", preset_id=None,
                         preset_name="기본", model="m", quality="low", size="1024x1024", background="auto",
                         params={}, request_id="req_1", usage={"output_tokens": 5}, est_cost_usd=0.01)
    ws.db.update_image(out, favorite=1, tags="금발, 정면", name="베스트")
    aid = ws.register_asset(ws.image_path(ws.db.get_image(out)), "Hair", "가발", "금발", source_image_id=out)
    assert ws.db.get_asset(aid)["source_original_id"] == oid
    proj_json = json.loads((ws.project_dir(pid) / "project.json").read_text(encoding="utf-8"))
    assert len(proj_json["images"]) == 3
    ws.close()
    ws2 = Workspace(Settings())  # 재시작
    row = ws2.db.get_image(out)
    assert row["favorite"] == 1 and row["tags"] == "금발, 정면" and row["name"] == "베스트"
    assert ws2.image_path(row).exists()
    assert len(ws2.db.list_images(pid, "output", favorites_only=True)) == 1
    # 참조 무결성: 원본 삭제 시 결과/에셋은 남고 참조만 끊긴다
    assert ws2.db.image_references(oid) == {"derived_outputs": 1, "assets": 1}
    ws2.delete_image(oid)
    assert ws2.db.get_image(out)["source_image_id"] is None
    a = ws2.db.get_asset(aid)
    assert a["source_original_id"] is None and ws2.paths.abs(a["rel_path"]).exists()
    ws2.close()


def test_prompt_edit_applies(ws):
    ws.save_default_prompt("front_view", "EDITED PROMPT")
    assert ws.resolve_prompt("front_view", None) == ("EDITED PROMPT", "기본")
    pid = ws.db.add_preset("내 프리셋", "front_view", "PRESET PROMPT", "gpt-image-2", "low", "1024x1024", "auto")
    assert ws.resolve_prompt("front_view", pid) == ("PRESET PROMPT", "내 프리셋")
    assert "straight-on" in ws.reset_default_prompt("front_view")


def test_pricing(ws):
    cat = ws.catalog
    e = cat.estimate("gpt-image-2.6-sunburst", "low", "1024x1024", "hello", 1, ws.db)
    assert not e.computable and "계산 불가" in e.headline()
    e = cat.estimate("gpt-image-2", "medium", "1024x1024", "hello", 1, ws.db)
    assert e.computable and e.basis == "reference" and 0.05 < e.typical < 0.06
    e = cat.estimate("unknown-model", "low", "1024x1024", "hello", 1, ws.db)
    assert not e.computable
    usage = {"input_tokens": 1100, "input_tokens_details": {"image_tokens": 1000, "text_tokens": 100},
             "output_tokens": 800}
    cost = cat.actual_cost("gpt-image-2.6-sunburst", usage)
    assert cost == pytest.approx((100 * 5 + 1000 * 8 + 800 * 30) / 1e6)
    assert cat.actual_cost("gpt-image-2.6-sunburst", None) is None
    for tokens in (800, 1000):
        ws.db.add_usage(model="gpt-image-2.6-sunburst", quality="low", size="1024x1024", n_inputs=1, status="ok",
                        input_image_tokens=1000, output_tokens=tokens, est_cost_usd=0.03)
    e = cat.estimate("gpt-image-2.6-sunburst", "low", "1024x1024", "hello", 1, ws.db)
    assert e.computable and e.basis == "history" and e.low < e.typical < e.high
    assert ws.db.usage_summary(ws.month_start())["cost"] == pytest.approx(0.06)


def test_option_validation(ws):
    from crs.pricing import check_custom_size
    cat = ws.catalog
    assert cat.validate("gpt-image-2", "high", "1024x1024", "auto") == []
    assert cat.validate("gpt-image-2", "max", "1024x1024", "auto")
    assert cat.validate("gpt-image-1.5", "high", "2048x2048", "auto")
    assert cat.validate("gpt-image-2.5-flare", "max", "2048x1152", "transparent") == []
    assert check_custom_size("1000x1000") and check_custom_size("4096x1024") and check_custom_size("abc")
    assert check_custom_size("512x512")  # 픽셀 수 부족


def _service(handler):
    from crs.services.image_service import ImageGenerationService
    return ImageGenerationService(lambda: FAKE_KEY, http_client=make_mock_client(handler))


def _req(path, **kw):
    from crs.services.image_service import GenerationRequest
    return GenerationRequest(input_images=[path], prompt="make it front", model="gpt-image-2.6-sunburst", **kw)


def test_service_real_sdk_request(sample_png):
    seen = []

    def handler(request):
        seen.append(request)
        return ok_response()

    r = _service(handler).generate(_req(sample_png, quality="low", size="1024x1024", background="transparent"))
    assert len(seen) == 1
    req = seen[0]
    assert req.url.path.endswith("/images/edits") and req.method == "POST"
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = req.content
    for needle in (b'name="model"', b"gpt-image-2.6-sunburst", b'name="prompt"', b"make it front", b'name="image',
                   b'name="quality"', b'name="size"', b'name="background"', b"transparent", b"\x89PNG"):
        assert needle in body, needle
    assert r.request_id == "req_test_123" and r.usage["output_tokens"] == 800
    assert Image.open(__import__("io").BytesIO(r.image_bytes)).size == (32, 32)


def test_service_auto_options_omitted_and_jpeg_passthrough(tmp_path):
    seen = []
    jpg = tmp_path / "in.jpg"
    Image.new("RGB", (40, 40), "blue").save(jpg)
    bmp = tmp_path / "in.bmp"
    Image.new("RGB", (40, 40), "blue").save(bmp)
    svc = _service(lambda request: (seen.append(request), ok_response())[1])
    svc.generate(_req(jpg))
    assert b'name="quality"' not in seen[0].content and b'name="size"' not in seen[0].content
    assert b"image/jpeg" in seen[0].content
    svc.generate(_req(bmp))  # 미지원 포맷은 PNG로 변환
    assert b"image/png" in seen[1].content and bmp.exists()


@pytest.mark.parametrize("resp,kind,uncertain", [
    (lambda: error_response(401, "invalid_api_key", "Incorrect API key provided"), "auth", False),
    (lambda: error_response(429, "insufficient_quota", "You exceeded your current quota"), "quota", False),
    (lambda: error_response(429, "rate_limit_exceeded", "Rate limit reached"), "rate_limit", False),
    (lambda: error_response(400, "invalid_value", "Invalid value: 'max'", param="quality"), "unsupported", False),
    (lambda: error_response(400, "moderation_blocked", "Your request was rejected by the safety system"),
     "moderation", False),
    (lambda: error_response(404, "model_not_found", "The model does not exist"), "not_found", False),
    (lambda: error_response(403, None, "Your organization must be verified to use the model"), "permission", False),
    (lambda: error_response(500, None, "server error", type_="server_error"), "server", True),
])
def test_service_error_mapping_no_retry(sample_png, resp, kind, uncertain):
    from crs.services.image_service import GenerationError
    calls = []
    svc = _service(lambda request: (calls.append(1), resp())[1])
    with pytest.raises(GenerationError) as ei:
        svc.generate(_req(sample_png))
    assert ei.value.kind == kind and ei.value.billing_uncertain == uncertain
    assert len(calls) == 1  # 자동 재시도 없음
    assert FAKE_KEY not in ei.value.message and FAKE_KEY not in ei.value.detail


def test_service_network_and_timeout(sample_png):
    import httpx2
    from crs.services.image_service import GenerationError
    calls = []

    def boom(request):
        calls.append(1)
        raise httpx2.ConnectError("no network")

    with pytest.raises(GenerationError) as ei:
        _service(boom).generate(_req(sample_png))
    assert ei.value.kind == "network" and ei.value.billing_uncertain and len(calls) == 1

    def slow(request):
        raise httpx2.ReadTimeout("timeout")

    with pytest.raises(GenerationError) as ei:
        _service(slow).generate(_req(sample_png))
    assert ei.value.kind == "timeout" and ei.value.billing_uncertain


def test_service_no_key(sample_png):
    from crs.services.image_service import GenerationError, ImageGenerationService
    with pytest.raises(GenerationError) as ei:
        ImageGenerationService(lambda: None).generate(_req(sample_png))
    assert ei.value.kind == "auth"


def test_list_image_models():
    import httpx2

    def handler(request):
        assert request.url.path.endswith("/models")
        return httpx2.Response(200, json={"object": "list", "data": [
            {"id": "gpt-image-2.6-flare", "object": "model", "created": 1, "owned_by": "openai"},
            {"id": "gpt-6", "object": "model", "created": 1, "owned_by": "openai"}]})

    assert _service(handler).list_image_models() == ["gpt-image-2.6-flare"]


def test_redaction_and_background_removal():
    from crs.logging_setup import redact
    from crs.workspace import remove_background_simple
    assert FAKE_KEY not in redact(f"error with {FAKE_KEY} inside") and "Bearer abcdefghijkl" not in redact(
        "Authorization: Bearer abcdefghijkl")
    im = Image.new("RGB", (60, 60), "white")
    for x in range(20, 40):
        for y in range(20, 40):
            im.putpixel((x, y), (10, 10, 10))
    out = remove_background_simple(im)
    assert out.getpixel((2, 2))[3] == 0 and out.getpixel((30, 30))[3] == 255


def test_markup_sent_in_request(sample_png, tmp_path):
    from crs.services.image_service import GenerationRequest
    seen = []
    mk = tmp_path / "markup.png"
    Image.new("RGBA", Image.open(sample_png).size, (0, 0, 0, 0)).save(mk)
    svc = _service(lambda request: (seen.append(request), ok_response())[1])
    svc.generate(GenerationRequest(input_images=[sample_png, sample_png], prompt="fix", model="gpt-image-2.6-sunburst",
                                   markup=mk))
    body = seen[0].content
    assert b'name="markup"' in body and body.count(b'name="image') == 2


def test_old_models_json_gets_markup_flag(ws):
    import json as _json
    data = _json.loads(ws.paths.models_file.read_text(encoding="utf-8"))
    for m in data["models"]:
        m.pop("markup", None)
    ws.paths.models_file.write_text(_json.dumps(data), encoding="utf-8")
    from crs.pricing import Catalog
    cat = Catalog(ws.paths.models_file)
    assert cat.get("gpt-image-2.6-sunburst")["markup"] and not cat.get("gpt-image-2")["markup"]
    assert "gpt-image-2.6-flare" in cat.markup_models() and not cat.get("unknown-x")["markup"]


def test_build_retouch_prompt(ws):
    from crs.ui.retouch_dialog import build_retouch_prompt
    t = ws.default_prompt("retouch")
    p = build_retouch_prompt(t, "머리카락 줄여줘", True, True)
    assert "머리카락 줄여줘" in p and "red-marked area" in p and "SECOND image" in p and "{" not in p
    p = build_retouch_prompt(t, "선 정리", False, False)
    assert "whole image" in p and "SECOND image" not in p
    assert "Requested change: X" in build_retouch_prompt("custom template", "X", False, False)

"""v0.3 기능: 이름 변경 이전, 화풍 추출, 파트 사면도, 복사, 충전액, 자동 업데이트."""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from conftest import FAKE_KEY, make_mock_client, responses_ok


# ---------------------------------------------------------------- 이름 변경 이전
def test_legacy_settings_and_data_migration(tmp_path, monkeypatch):
    from crs import config
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    old_cfg = tmp_path / "oldcfg"
    old_cfg.mkdir()
    old_root = home / "Documents" / "CharacterReferenceStudio"
    (old_root / "data").mkdir(parents=True)
    (old_root / "data" / "marker.txt").write_text("keep")
    (old_cfg / "settings.json").write_text(json.dumps({"data_root": str(old_root), "monthly_budget_usd": 3.0}))
    monkeypatch.setenv("CRS_CONFIG_DIR", str(tmp_path / "newcfg"))
    monkeypatch.setenv("CRS_LEGACY_CONFIG_DIR", str(old_cfg))
    s = config.Settings()
    new_root = home / "Documents" / "V-Concept Studio"
    assert Path(s.get("data_root")) == new_root and (new_root / "data" / "marker.txt").read_text() == "keep"
    assert not old_root.exists() and s.get("monthly_budget_usd") == 3.0
    assert s.get("last_run_version") == "0.2.0" and s.migrated_from == str(old_cfg)
    assert (old_cfg / "settings.json").exists()  # 옛 설정은 지우지 않는다
    s2 = config.Settings()  # 두 번째 실행에서는 다시 이전하지 않는다
    assert s2.migrated_from is None and Path(s2.get("data_root")) == new_root


def test_legacy_keyring_migration(monkeypatch):
    from crs import keystore

    class FakeKR:
        def __init__(self):
            self.store = {(keystore.LEGACY_SERVICE, keystore.ACCOUNT): FAKE_KEY}

        def get_password(self, s, a):
            return self.store.get((s, a))

        def set_password(self, s, a, v):
            self.store[(s, a)] = v

        def delete_password(self, s, a):
            self.store.pop((s, a), None)

    fake = FakeKR()
    monkeypatch.setattr(keystore, "_secure_backend", lambda: fake)
    assert keystore.get_key() == FAKE_KEY
    assert fake.store == {(keystore.SERVICE, keystore.ACCOUNT): FAKE_KEY}


# ---------------------------------------------------------------- 화풍 추출
def test_style_service_request_and_parse(sample_png):
    from crs.services.style_service import StyleExtractionService
    seen = []
    svc = StyleExtractionService(lambda: FAKE_KEY, http_client=make_mock_client(
        lambda r: (seen.append(r), responses_ok())[1]))
    res = svc.extract(sample_png, "gpt-6-luna", "ANALYZE STYLE", "low")
    body = json.loads(seen[0].content)
    assert seen[0].url.path.endswith("/responses")
    assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "low"}
    content = body["input"][0]["content"]
    assert content[0]["text"] == "ANALYZE STYLE" and content[1]["image_url"].startswith("data:image/png;base64,")
    assert body["text"]["format"]["type"] == "json_schema"
    assert res.prompt_en == "clean thin line art, soft cel shading" and res.summary_ko == "- 얇은 선화"
    assert res.usage["output_tokens"] == 900 and res.request_id == "req_style_1"


def test_style_pricing(ws):
    cat = ws.catalog
    assert ws.style_model() == "gpt-6-luna"
    assert cat.text_cost("gpt-6-luna", {"input_tokens": 1500, "output_tokens": 900}) == pytest.approx(
        (1500 * 0.10 + 900 * 0.50) / 1e6)
    est = cat.text_estimate("gpt-6-luna", ws.db)
    assert est.computable and est.typical < 0.01
    assert not cat.text_estimate("unknown-text", ws.db).computable


def test_isolate_part():
    from crs.workspace import isolate_part
    im = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
    for x in range(20, 60):
        for y in range(20, 60):
            im.putpixel((x, y), (255, 0, 0, 255))        # 칠할 파트
    for x in range(120, 180):
        for y in range(120, 180):
            im.putpixel((x, y), (0, 0, 255, 255))        # 칠하지 않을 파트
    painted = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    for x in range(15, 65):
        for y in range(15, 65):
            painted.putpixel((x, y), (255, 0, 0, 255))
    out = isolate_part(im, painted)
    colors = {c for _, c in out.getcolors(10000)}
    assert (255, 0, 0) in colors and (0, 0, 255) not in colors
    assert out.width < 120 and out.getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(ValueError):
        isolate_part(im, Image.new("RGBA", (200, 200), (0, 0, 0, 0)))


# ---------------------------------------------------------------- 업데이트
def test_version_and_changelog():
    from crs import APP_VERSION, updater
    assert updater.is_newer("v0.10.0", "0.9.9") and not updater.is_newer("v0.3.0", "0.3.0")
    assert updater.parse_version("V-Concept Studio v1.2") == (1, 2, 0)
    section = updater.changelog_section(APP_VERSION)
    assert "파트 사면도" in section and "## " not in section
    assert updater.changelog_section("9.9.9") == ""


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _release_zip(tmp_path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("VConceptStudio/VConceptStudio.exe", b"new exe")
        z.writestr("VConceptStudio/_internal/lib.dll", b"lib")
    return buf.getvalue()


def test_fetch_check_and_prepare_update(tmp_path, monkeypatch):
    from crs import updater
    zdata = _release_zip(tmp_path)
    sha = hashlib.sha256(zdata).hexdigest()
    release = {"tag_name": "v9.0.0", "body": "## 바뀐 점\n- 무언가", "html_url": "https://github.com/x/releases/v9",
               "draft": False, "prerelease": False,
               "assets": [{"name": updater.ASSET_ZIP, "browser_download_url": "https://dl/zip", "size": len(zdata)},
                          {"name": updater.ASSET_ZIP + ".sha256", "browser_download_url": "https://dl/sha"}]}
    responses = {updater.API_URL: json.dumps(release).encode(), "https://dl/zip": zdata,
                 "https://dl/sha": f"{sha}  {updater.ASSET_ZIP}\n".encode()}
    monkeypatch.setattr(updater, "_open", lambda url, timeout, accept=None: FakeResp(responses[url]))
    info = updater.check_latest()
    assert info.version == "9.0.0" and info.zip_url == "https://dl/zip" and info.sha256_url
    assert updater.check_latest(skipped="9.0.0") is None

    # 소스 실행 중에는 자동 설치 거부
    with pytest.raises(RuntimeError, match="소스 코드"):
        updater.prepare_update(info)

    app_dir = tmp_path / "app dir 한글"
    app_dir.mkdir()
    (app_dir / "VConceptStudio.exe").write_bytes(b"old")
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater.sys, "executable", str(app_dir / "VConceptStudio.exe"))
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    progress = []
    script = updater.prepare_update(info, lambda d, t: progress.append((d, t)))
    text = script.read_text(encoding="utf-8-sig")
    assert script.read_bytes().startswith(b"\xef\xbb\xbf")
    assert str(app_dir) in text and "robocopy" in text and "--updated" in text and "Wait-Process -Id" in text
    src = Path(text.split("$src = '")[1].split("'")[0])
    assert (src / "VConceptStudio.exe").read_bytes() == b"new exe" and progress[-1][0] == len(zdata)

    # 해시가 다르면 설치하지 않는다
    responses["https://dl/sha"] = b"0" * 64
    with pytest.raises(RuntimeError, match="손상"):
        updater.prepare_update(info)


def test_no_release_yet(monkeypatch):
    import urllib.error
    from crs import updater

    def raise_404(url, timeout, accept=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(updater, "_open", raise_404)
    assert updater.check_latest() is None

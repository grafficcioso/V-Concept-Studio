"""구글 드라이브 동기화(PC): 가짜 드라이브 서버로 두 대의 PC 사이 왕복을 검증."""
import json
import threading
import urllib.parse
import urllib.request

import pytest
from PIL import Image

from conftest import png_bytes
from fake_drive import FakeDrive


@pytest.fixture
def drive():
    d = FakeDrive().start()
    yield d
    d.stop()


def make_ws(tmp_path, name, monkeypatch):
    from crs.config import Settings
    from crs.workspace import Workspace
    monkeypatch.setenv("CRS_CONFIG_DIR", str(tmp_path / f"cfg_{name}"))
    s = Settings()
    s.set("data_root", str(tmp_path / f"data_{name}"))
    return Workspace(s)


def engine(ws, drive):
    from crs.sync.drive import DriveClient
    from crs.sync.engine import SyncEngine
    return SyncEngine(ws, DriveClient(lambda: "tok", api=drive.base + "/drive/v3", upload=drive.base + "/upload/drive/v3"))


def add_output(ws, pid, name="", color=(255, 0, 0, 255)):
    iid = ws.save_output(pid, png_bytes((40, 30), color), function="front_view", source_image_id=None, prompt="p",
                         preset_id=None, preset_name="기본", model="gpt-image-2.6-sunburst", quality="low", size="auto",
                         background="auto", params={}, request_id="req_1", usage=None, est_cost_usd=0.01)
    if name:
        ws.db.update_image(iid, name=name)
    return iid


def test_two_pcs_roundtrip(tmp_path, monkeypatch, drive):
    a = make_ws(tmp_path, "a", monkeypatch)
    pid = a.create_project("캐릭터A")
    out_id = add_output(a, pid, "정면 베스트")
    asset_id = a.register_asset(Image.new("RGBA", (20, 20), (0, 255, 0, 255)), "Hair", "트윈테일", "핑크")
    st = engine(a, drive).sync()
    assert st.uploaded == 2 and not st.errors
    metas = sorted((json.loads(f["description"]) for f in drive.items().values()), key=lambda m: m["kind"])
    assert [m["kind"] for m in metas] == ["asset", "output"]
    assert metas[0]["category"] == "Hair" and metas[0]["name"] == "트윈테일"
    assert metas[1]["project"] == "캐릭터A" and metas[1]["name"] == "정면 베스트" and metas[1]["function"] == "front_view"
    assert engine(a, drive).sync().uploaded == 0          # 다시 해도 중복 업로드 없음

    b = make_ws(tmp_path, "b", monkeypatch)
    st = engine(b, drive).sync()
    assert st.downloaded == 2 and not st.errors
    proj = b.db.get_project_by_name("캐릭터A")
    outs = b.db.list_images(proj["id"], "output")
    assert len(outs) == 1 and outs[0]["name"] == "정면 베스트" and b.image_path(outs[0]).exists()
    assets = b.db.list_assets("Hair")
    assert len(assets) == 1 and assets[0]["tags"] == "핑크"

    # B 에서 이름·즐겨찾기 변경 → A 로 전달
    b.db.update_image(outs[0]["id"], name="B가 고침", favorite=1)
    assert engine(b, drive).sync().meta_pushed == 1
    assert engine(a, drive).sync().meta_pulled == 1
    row = a.db.get_image(out_id)
    assert row["name"] == "B가 고침" and row["favorite"] == 1

    # A 에서 에셋 삭제 → 드라이브 휴지통 → B 에서도 삭제
    a.delete_asset(asset_id)
    st = engine(a, drive).sync()
    assert st.trashed_remote == 1 and len(drive.items()) == 1
    st = engine(b, drive).sync()
    assert st.deleted_local == 1 and b.db.list_assets() == []
    a.close()
    b.close()


def test_conflict_later_edit_wins(tmp_path, monkeypatch, drive):
    a = make_ws(tmp_path, "a", monkeypatch)
    add_output(a, a.create_project("P"))
    engine(a, drive).sync()
    b = make_ws(tmp_path, "b", monkeypatch)
    engine(b, drive).sync()
    ia = a.db.list_images(a.db.get_project_by_name("P")["id"], "output")[0]["id"]
    ib = b.db.list_images(b.db.get_project_by_name("P")["id"], "output")[0]["id"]
    a.db.update_image(ia, name="A", meta_updated="2099-01-01T10:00:00Z")
    b.db.update_image(ib, name="B", meta_updated="2099-01-01T11:00:00Z")
    engine(a, drive).sync()            # A 먼저 올림
    engine(b, drive).sync()            # B 가 더 늦음 → B 가 올림
    engine(a, drive).sync()            # A 가 받음
    assert a.db.get_image(ia)["name"] == "B" and b.db.get_image(ib)["name"] == "B"


def test_switching_account_does_not_delete_local(tmp_path, monkeypatch, drive):
    a = make_ws(tmp_path, "a", monkeypatch)
    add_output(a, a.create_project("P"))
    engine(a, drive).sync()
    other = FakeDrive().start()       # 다른 구글 계정(빈 드라이브)
    try:
        st = engine(a, other).sync()
        assert st.deleted_local == 0 and st.uploaded == 1 and len(other.items()) == 1
    finally:
        other.stop()


def test_partial_failure_reported_and_retried(tmp_path, monkeypatch, drive):
    a = make_ws(tmp_path, "a", monkeypatch)
    pid = a.create_project("P")
    add_output(a, pid, "one")
    add_output(a, pid, "two")
    drive.fail_next = ("POST", r"/upload/", 500)
    st = engine(a, drive).sync()
    assert st.uploaded == 1 and len(st.errors) == 1
    st = engine(a, drive).sync()
    assert st.uploaded == 1 and not st.errors and len(drive.items()) == 2


def test_pc_google_sign_in_flow(drive):
    from crs.sync.drive import GoogleAuth
    store = {}
    auth = GoogleAuth("cid.apps.googleusercontent.com", "secret", lambda: store.get("rt"),
                      lambda v: store.__setitem__("rt", v), token_url=drive.base + "/token")
    seen = {}

    def fake_browser(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        seen.update({k: v[0] for k, v in q.items()})
        redirect = q["redirect_uri"][0]

        def hit():
            urllib.request.urlopen(f"{redirect}/?code=abc&state={q['state'][0]}").read()

        threading.Thread(target=hit).start()

    auth.sign_in(open_browser=fake_browser, timeout=10)
    assert store["rt"] == "rt_fake" and auth.is_connected()
    assert seen["scope"].endswith("/auth/drive.file") and seen["code_challenge_method"] == "S256"
    assert seen["access_type"] == "offline" and seen["redirect_uri"].startswith("http://127.0.0.1:")
    assert auth.access_token().startswith("at")


def test_pc_ui_background_sync(tmp_path, monkeypatch, drive):
    """메인 창: 생성 후 자동 동기화 예약 → 백그라운드 스레드(별도 DB 연결)로 올림 → 상단 표시 갱신."""
    import time
    from PySide6.QtWidgets import QApplication, QMessageBox
    from crs.sync.drive import DriveClient
    from crs.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    for n in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, n, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    ws = make_ws(tmp_path, "ui", monkeypatch)
    pid = ws.create_project("UI")
    add_output(ws, pid, "첫 결과")
    win = MainWindow(ws)
    win.sync.drive_factory = lambda clone: DriveClient(lambda: "tok", api=drive.base + "/drive/v3",
                                                       upload=drive.base + "/upload/drive/v3")
    win.show()
    assert win.sync.sync_now()
    end = time.time() + 15
    while (win.sync.is_busy() or "올림" not in win.gen.sync_btn.text()) and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert "올림 1" in win.gen.sync_btn.text() and len(drive.items()) == 1
    # 다른 기기에서 올라온 결과가 생기면 받아서 목록에 보인다
    other = make_ws(tmp_path, "other", monkeypatch)
    add_output(other, other.create_project("UI"), "다른 기기 결과", (0, 0, 255, 255))
    engine(other, drive).sync()
    win.sync.sync_now()
    end = time.time() + 15
    while (win.sync.is_busy() or "받음 1" not in win.gen.sync_btn.text()) and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert "받음 1" in win.gen.sync_btn.text()
    assert win.gen.history.count() == 2
    win.close()
    other.close()

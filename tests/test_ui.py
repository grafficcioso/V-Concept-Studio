"""오프스크린 UI 통합 테스트: 실제 위젯 + (모의 HTTP 뒤의) 실제 OpenAI SDK 경로."""
import time

import pytest
from PIL import Image
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from conftest import FAKE_KEY, error_response, make_mock_client, ok_response


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp, ws, monkeypatch):
    shown = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: shown.append(a[2]) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    from crs.ui.main_window import MainWindow
    w = MainWindow(ws)
    w.messages = shown
    w.show()
    qapp.processEvents()
    yield w
    w.ctl.wait(5000)
    w.close()


def wait_idle(qapp, win, timeout=15):
    end = time.time() + timeout
    while win.ctl.is_busy() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()
    assert not win.ctl.is_busy()


def setup_project(win, ws, sample_png):
    pid = ws.create_project("캐릭터A")
    win.gen.reload_projects(pid)
    win.gen.import_files([str(sample_png)])
    assert win.gen.input_id and win.gen.input_panel.view.has_image()
    return pid


def use_mock_api(ws, handler):
    from crs import keystore
    from crs.services.image_service import ImageGenerationService
    keystore.set_key(FAKE_KEY)
    ws._real_service = ImageGenerationService(keystore.get_key, http_client=make_mock_client(handler))
    ws.settings.set("confirm_before_request", False)


def test_no_key_blocks_generation_but_ui_works(qapp, win, ws, sample_png):
    setup_project(win, ws, sample_png)
    win.gen.generate()
    assert not win.ctl.is_busy() and any("API 키" in m for m in win.messages)
    assert "API 키 없음" in win.gen.api_status.text()


def test_three_buttons_real_api_path(qapp, win, ws, sample_png):
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    pid = setup_project(win, ws, sample_png)
    win.gen.refresh_api_status()
    assert "등록됨" in win.gen.api_status.text()
    ws.save_default_prompt("four_views", "CUSTOM FOUR VIEW PROMPT")
    for key in ("front_view", "parts_separation", "four_views"):
        win.gen.fn_buttons[key].click()
        win.gen.generate()
        assert win.ctl.is_busy() and not win.gen.generate_btn.isEnabled()
        wait_idle(qapp, win)
    assert len(seen) == 3 and all(r.url.path.endswith("/images/edits") for r in seen)
    assert b"perfect straight-on front view" in seen[0].content
    assert b"character-parts separation sheet" in seen[1].content
    assert b"CUSTOM FOUR VIEW PROMPT" in seen[2].content  # 수정한 프롬프트가 다음 요청에 적용
    outs = ws.db.list_images(pid, "output")
    assert len(outs) == 3 and win.gen.history.count() == 3
    row = outs[0]
    assert row["request_id"] == "req_test_123" and row["est_cost_usd"] > 0 and row["source_image_id"] == win.gen.input_id
    assert ws.image_path(row).exists() and win.gen.result_panel.view.has_image()
    assert "앱 추정 실제 비용" in win.gen.result_info.text()
    usage = ws.db.usage_rows()
    assert [u["status"] for u in usage] == ["ok"] * 3 and usage[0]["output_tokens"] == 800
    assert "예상 약" in win.gen.estimate_label.text()  # 사용 기록 기반 추정으로 전환
    # 결과를 다음 입력으로 → 즐겨찾기 → 에셋 등록
    win.gen.use_result_as_input()
    assert win.gen.input_id == win.gen.result_id
    win.gen.toggle_favorite()
    assert ws.db.get_image(win.gen.result_id)["favorite"] == 1
    win.usage.reload()
    assert win.usage.table.rowCount() == 3


def test_failure_does_not_crash_and_no_retry(qapp, win, ws, sample_png):
    calls = []
    use_mock_api(ws, lambda request: (calls.append(1), error_response(401, "invalid_api_key", "Incorrect API key"))[1])
    setup_project(win, ws, sample_png)
    win.gen.generate()
    wait_idle(qapp, win)
    assert len(calls) == 1 and any("API 키가 올바르지" in m for m in win.messages)
    assert win.gen.generate_btn.isEnabled() and win.isVisible()
    assert ws.db.usage_rows()[0]["status"] == "failed"

    import httpx2

    def boom(request):
        raise httpx2.ConnectError("offline")

    use_mock_api(ws, boom)
    win.gen.generate()
    wait_idle(qapp, win)
    assert ws.db.usage_rows()[0]["status"] == "unknown" and any("네트워크" in m for m in win.messages)


def test_unsupported_option_blocked_before_request(qapp, win, ws, sample_png):
    calls = []
    use_mock_api(ws, lambda request: (calls.append(1), ok_response())[1])
    setup_project(win, ws, sample_png)
    win.gen.model_combo.setCurrentIndex(win.gen.model_combo.findData("gpt-image-1.5"))
    assert win.gen.quality_combo.findText("max") < 0 and not win.gen.size_combo.isEditable()
    assert win.gen.fidelity_combo.isEnabled()
    win.gen.model_combo.setCurrentIndex(win.gen.model_combo.findData("gpt-image-2.6-sunburst"))
    assert win.gen.quality_combo.findText("max") >= 0 and win.gen.size_combo.isEditable()
    assert not win.gen.fidelity_combo.isEnabled()
    win.gen.size_combo.setEditText("1000x999")
    win.gen.generate()
    assert not win.ctl.is_busy() and calls == [] and any("16의 배수" in m for m in win.messages)


def test_demo_mode_and_clipboard_paste(qapp, win, ws):
    ws.settings.set("demo_mode", True)
    ws._demo_service.delay = 0.1
    pid = ws.create_project("데모")
    win.gen.reload_projects(pid)
    img = QImage(120, 90, QImage.Format.Format_ARGB32)
    img.fill(QColor("orange"))
    QGuiApplication.clipboard().setImage(img)
    win.gen.paste()
    assert len(ws.db.list_images(pid, "original")) == 1
    win.gen.generate()
    wait_idle(qapp, win)
    assert ws.db.usage_rows()[0]["status"] == "demo" and len(ws.db.list_images(pid, "output")) == 1


def test_budget_confirmation_blocks(qapp, win, ws, sample_png, monkeypatch):
    calls = []
    use_mock_api(ws, lambda request: (calls.append(1), ok_response())[1])
    setup_project(win, ws, sample_png)
    ws.settings.set("monthly_budget_usd", 0.01)
    ws.db.add_usage(model="x", status="ok", est_cost_usd=0.5)
    texts = []

    def fake_exec(self):
        texts.append(self.informativeText())
        return 0  # 아무 버튼도 누르지 않음 = 취소

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    win.gen.generate()
    assert not win.ctl.is_busy() and calls == []
    assert "소프트 예산을 넘습니다" in texts[0] and "청구를 차단하지 않습니다" in texts[0]


def test_library_compose_export_and_crop(qapp, win, ws, sample_png, tmp_path):
    pid = setup_project(win, ws, sample_png)
    part = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(30, 70):
        for y in range(30, 70):
            part.putpixel((x, y), (255, 0, 0, 255))
    aid = ws.register_asset(part, "Accessories", "리본", "빨강")
    aid2 = ws.register_asset(sample_png, "Head", "머리")
    win.library.reload()
    assert win.library.grid.count() == 2
    win.library.search.setText("리본")
    assert win.library.grid.count() == 1
    win.library.search.setText("")
    comp = win.compose
    comp.set_canvas(400, 300, "transparent")
    assert comp.add_asset(aid2) and comp.add_asset(aid)
    top = comp.layers[-1]
    top.setSelected(True)
    comp.sx.setValue(10), comp.sy.setValue(20), comp.ss.setValue(150), comp.sr.setValue(45)
    assert top.pos().x() == 10 and top.scale() == 1.5 and top.rotation() == 45
    comp._move(-1)
    assert comp.layers[0] is top
    comp._move(+1)
    comp.layers[0].setVisible(False)
    img = comp.render_image()
    assert img.width() == 400 and img.height() == 300
    assert img.pixelColor(399, 299).alpha() == 0          # 투명 유지
    assert img.pixelColor(60, 70).red() > 200             # 변형된 리본 픽셀
    out = tmp_path / "comp.png"
    assert img.save(str(out), "PNG") and Image.open(out).mode == "RGBA"
    # 저장 → 비우기 → 불러오기
    data = comp.to_dict()
    cid = ws.db.save_composition(None, pid, "테스트 조합", data)
    comp.clear_layers()
    assert comp.load_dict(__import__("json").loads(ws.db.get_composition(cid)["json"])) == []
    assert len(comp.layers) == 2 and comp.layers[-1].rotation() == 45 and not comp.layers[0].isVisible()
    assert ws.db.compositions_using_asset(aid) == ["테스트 조합"]
    # 시트에서 영역 잘라 에셋 등록
    from crs.ui.dialogs import CropDialog
    dlg = CropDialog(sample_png, "Hair", "조각")
    dlg.crop.set_selection(QRect(80, 100, 150, 200))
    dlg.remove_bg.setChecked(True)
    piece = dlg.result_image()
    assert piece.size == (150, 200) and piece.getpixel((2, 2))[3] == 0 and piece.getpixel((60, 80))[3] == 255
    ws.register_asset(piece, "Hair", "조각")
    assert len(ws.db.list_assets("Hair")) == 1


def test_ai_compose_sends_preview_and_parts(qapp, win, ws, sample_png):
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    pid = setup_project(win, ws, sample_png)
    a1 = ws.register_asset(sample_png, "Head", "머리")
    a2 = ws.register_asset(Image.new("RGBA", (50, 50), (0, 255, 0, 255)), "Hair", "헤어")
    win.compose.add_asset(a1), win.compose.add_asset(a2)
    win.compose.ai_compose()
    wait_idle(qapp, win)
    assert len(seen) == 1 and seen[0].content.count(b'name="image') == 3  # 미리보기 + 부품 2
    assert b"layout mock-up" in seen[0].content
    out = ws.db.list_images(pid, "output")[0]
    assert out["function"] == "ai_compose" and win.tabs.currentWidget() is win.gen


def test_settings_dialog_and_no_key_leak(qapp, win, ws, sample_png, monkeypatch):
    from crs import keystore
    from crs.ui.settings_dialog import SettingsDialog
    use_mock_api(ws, lambda request: ok_response())
    setup_project(win, ws, sample_png)
    win.gen.generate()
    wait_idle(qapp, win)
    dlg = SettingsDialog(ws, win, "prompts:parts_separation")
    assert dlg.fn_combo.currentData() == "parts_separation"
    assert "invented accessories" in dlg.neg_edit.toPlainText()     # 기본 Negative 가 아래 칸에 나뉘어 보인다
    assert "### Negative" not in dlg.prompt_edit.toPlainText()
    dlg.prompt_edit.setPlainText("NEW PARTS PROMPT")
    dlg.neg_edit.setPlainText("labels, text")
    dlg._save_prompt()
    assert ws.default_prompt("parts_separation") == "NEW PARTS PROMPT\n\n### Negative\nlabels, text"
    monkeypatch.setattr("crs.ui.settings_dialog.QInputDialog.getText", staticmethod(lambda *a, **k: ("복제본", True)))
    dlg._duplicate_prompt()
    assert [p["name"] for p in ws.db.list_presets("parts_separation")] == ["복제본"]
    dlg.preset_list.setCurrentRow(0)
    dlg._reset_prompt()
    assert "character-parts separation sheet" in ws.default_prompt("parts_separation")
    dlg.key_edit.setText("sk-another-SECRET-0987654321")
    dlg._save_key()
    assert keystore.get_key() == "sk-another-SECRET-0987654321" and "SECRET" not in dlg.key_state.text()
    dlg.budget.setValue(12.5)
    dlg.table.item(0, 10).setText("31")
    dlg._save_and_close()
    assert ws.settings.get("monthly_budget_usd") == 12.5
    assert ws.catalog.get(ws.catalog.model_ids()[0])["pricing"]["image_output"] == 31.0
    assert dlg.result() == QDialog.DialogCode.Accepted
    ws.db.conn.commit()
    # API 키가 설정/DB/로그/프로젝트 파일/이미지 메타데이터 어디에도 없어야 한다
    import logging
    for h in logging.getLogger().handlers:
        h.flush()
    roots = [ws.paths.root, ws.settings.path.parent]
    scanned = 0
    for root in roots:
        for f in root.rglob("*"):
            if f.is_file():
                data = f.read_bytes()
                scanned += 1
                for secret in (FAKE_KEY, "sk-another-SECRET-0987654321"):
                    assert secret.encode() not in data, f
    assert scanned > 8


def test_cancel_request(qapp, win, ws, sample_png):
    ws.settings.set("demo_mode", True)
    ws._demo_service.delay = 30
    pid = setup_project(win, ws, sample_png)
    win.gen.generate()
    assert win.ctl.is_busy() and win.gen.cancel_btn.isVisibleTo(win.gen)
    win.gen.cancel_btn.click()
    wait_idle(qapp, win, timeout=5)
    assert ws.db.list_images(pid, "output") == [] and any("취소" in m for m in win.messages)
    assert win.gen.generate_btn.isEnabled()


def _paint_and_send(qapp, win, ws, monkeypatch, image_id, request, paint=True, use_original=True, accept=True):
    from PySide6.QtCore import QPointF
    from crs.ui import retouch_dialog
    captured = {}

    def fake_exec(self):
        if paint:
            c = self.canvas
            c.brush = 20
            c._stroke(QPointF(10, 10), QPointF(60, 60))
        self.request.setPlainText(request)
        self.use_original.setChecked(use_original)
        captured["dlg"] = self
        return 1 if accept else 0

    monkeypatch.setattr(retouch_dialog.RetouchDialog, "exec", fake_exec)
    win.gen.retouch(image_id)
    wait_idle(qapp, win)
    return captured["dlg"]


def test_retouch_with_markup_and_original_reference(qapp, win, ws, sample_png, monkeypatch):
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    pid = setup_project(win, ws, sample_png)
    win.gen.generate()  # 정면화 결과 1장
    wait_idle(qapp, win)
    front_id = win.gen.result_id
    _paint_and_send(qapp, win, ws, monkeypatch, front_id, "머리카락 가닥 줄여줘")
    body = seen[-1].content
    assert b'name="markup"' in body and body.count(b'name="image') == 2  # 결과 + 원본 원화
    assert "머리카락 가닥 줄여줘".encode() in body and b"red-marked area" in body
    out = ws.db.list_images(pid, "output")[0]
    assert out["function"] == "retouch" and out["source_image_id"] == front_id
    params = __import__("json").loads(out["params_json"])
    mk = ws.paths.abs(params["markup_file"])
    im = Image.open(mk)
    src = Image.open(ws.image_path(ws.db.get_image(front_id)))
    assert im.size == src.size and im.mode == "RGBA"
    assert im.getpixel((20, 20))[0] == 255 and 0 < im.getpixel((20, 20))[3] < 255 and im.getpixel((2, 30))[3] == 0
    assert front_id not in win.gen._retouch_states  # 성공하면 칠한 상태 정리


def test_retouch_whole_image_and_unsupported_model(qapp, win, ws, sample_png, monkeypatch):
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    setup_project(win, ws, sample_png)
    oid = win.gen.input_id
    # 원본 자체를 수정: 원본 참조 없음, 칠하지 않음 → 전체 수정
    _paint_and_send(qapp, win, ws, monkeypatch, oid, "선 정리", paint=False)
    body = seen[-1].content
    assert b'name="markup"' not in body and body.count(b'name="image') == 1 and b"whole image" in body
    # markup 미지원 모델 + 칠함 → 요청 전 차단, 칠한 상태 보존
    win.gen.model_combo.setCurrentIndex(win.gen.model_combo.findData("gpt-image-2"))
    n = len(seen)
    _paint_and_send(qapp, win, ws, monkeypatch, oid, "수정")
    assert len(seen) == n and any("지원하지 않습니다" in m for m in win.messages)
    assert oid in win.gen._retouch_states


def test_retouch_canvas_undo_eraser(qapp, sample_png):
    from PySide6.QtCore import QPointF
    from crs.ui.retouch_dialog import RetouchDialog
    dlg = RetouchDialog(sample_png, has_original=False, model_label="m", model_supports_markup=True)
    c = dlg.canvas
    assert not c.has_marks() and not dlg.use_original.isEnabled()
    c.undo_stack.append(c.overlay.copy())
    c._stroke(QPointF(50, 50), QPointF(100, 100))
    assert c.has_marks()
    c.eraser = True
    c.brush = 400
    c._stroke(QPointF(150, 200), QPointF(150, 200))
    assert not c.has_marks()
    c.undo()
    assert not c.has_marks()  # 되돌리기 = 칠하기 전 상태


# ======================================================================= v0.3 UI
def test_copy_result_to_clipboard(qapp, win, ws, sample_png):
    from PySide6.QtGui import QGuiApplication
    use_mock_api(ws, lambda request: ok_response())
    setup_project(win, ws, sample_png)
    win.gen.generate()
    wait_idle(qapp, win)
    QGuiApplication.clipboard().clear()
    win.gen.copy_result()
    mime = QGuiApplication.clipboard().mimeData()
    assert mime.hasImage() and mime.data("image/png").size() > 0
    assert QGuiApplication.clipboard().image().width() == 32


def test_style_extract_save_and_apply(qapp, win, ws, sample_png, monkeypatch):
    from conftest import responses_ok
    from crs import keystore
    from crs.services.style_service import StyleExtractionService
    from crs.ui import style_ui
    seen_img = []
    use_mock_api(ws, lambda request: (seen_img.append(request), ok_response())[1])
    ws._real_style_service = StyleExtractionService(keystore.get_key, http_client=make_mock_client(
        lambda r: responses_ok("SOFT PASTEL STYLE")))
    setup_project(win, ws, sample_png)

    def fake_exec(self):
        assert self.prompt.toPlainText() == "SOFT PASTEL STYLE"
        monkeypatch.setattr(style_ui.QInputDialog, "getText", staticmethod(lambda *a, **k: ("파스텔", True)))
        self.save()
        return 0

    monkeypatch.setattr(style_ui.StyleResultDialog, "exec", fake_exec)
    win.gen.extract_style(win.gen.input_id)
    end = time.time() + 10
    while win.gen.style_ctl.is_busy() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        qapp.processEvents()
    u = ws.db.usage_rows()[0]
    assert u["function"] == "style_extract" and u["status"] == "ok" and u["est_cost_usd"] > 0
    assert win.gen.style_combo.currentText() == "파스텔"
    win.gen.generate()
    wait_idle(qapp, win)
    assert b"Art style to follow" in seen_img[-1].content and b"SOFT PASTEL STYLE" in seen_img[-1].content
    win.gen.style_combo.setCurrentIndex(0)
    win.gen.generate()
    wait_idle(qapp, win)
    assert b"SOFT PASTEL STYLE" not in seen_img[-1].content


def test_part_views_sends_only_painted_part(qapp, win, ws, sample_png, monkeypatch):
    from PySide6.QtCore import QPointF
    from crs.ui import retouch_dialog
    seen = []
    use_mock_api(ws, lambda request: (seen.append(request), ok_response())[1])
    pid = setup_project(win, ws, sample_png)
    win.gen.generate()  # 파트 분할 결과라고 가정
    wait_idle(qapp, win)
    sheet_id = win.gen.result_id
    sheet = ws.image_path(ws.db.get_image(sheet_id))
    big = Image.new("RGB", (400, 300), "white")
    for x in range(30, 130):
        for y in range(40, 200):
            big.putpixel((x, y), (120, 60, 20))   # 헤어
    for x in range(250, 350):
        for y in range(60, 220):
            big.putpixel((x, y), (250, 210, 190))  # 헤드
    big.save(sheet)

    def fake_exec(self):
        idx = self.part_type.findData("part_views_hair")
        self.part_type.setCurrentIndex(idx)
        self.canvas.brush = 60
        self.canvas._stroke(QPointF(80, 60), QPointF(80, 180))
        self.use_original.setChecked(True)
        return 1

    monkeypatch.setattr(retouch_dialog.PartViewsDialog, "exec", fake_exec)
    win.gen.part_views(sheet_id)
    wait_idle(qapp, win)
    body = seen[-1].content
    assert body.count(b'name="image') == 2 and b"hairstyle part" in body and b"original full character" in body
    out = ws.db.list_images(pid, "output")[0]
    assert out["function"] == "part_views_hair"
    part = ws.db.get_image(out["source_image_id"])
    assert part["kind"] == "part_input" and part["source_image_id"] == sheet_id
    pim = Image.open(ws.image_path(part)).convert("RGB")
    cols = {c for _, c in pim.getcolors(100000)}
    assert (120, 60, 20) in cols and (250, 210, 190) not in cols
    assert ws.root_original_id(out["id"]) == win.gen.input_id or ws.root_original_id(out["id"]) is not None
    assert len(ws.db.list_images(pid, "original")) == 1  # 잘라낸 입력은 원본 목록에 안 나온다
    assert win.gen.history.count() == 2


def test_credit_display(qapp, win, ws):
    win.gen.refresh_credit()
    assert "미설정" in win.gen.credit_btn.text()
    ws.settings.set("credit_total_usd", 5.0)
    ws.settings.set("credit_since", "2000-01-01")
    ws.db.add_usage(model="m", status="ok", est_cost_usd=0.25)
    ws.db.add_usage(model="m", status="unknown", est_cost_usd=None)
    win.gen.refresh_credit()
    assert "추정 잔액 $4.75" in win.gen.credit_btn.text() and "과금 불명 1건" in win.gen.credit_btn.toolTip()
    win.usage.reload()
    assert "추정 잔액 $4.750" in win.usage.summary.text()


def test_post_update_changelog_shown(qapp, win, ws, monkeypatch):
    from crs.ui import update_ui
    shown = []
    monkeypatch.setattr(update_ui.ChangelogDialog, "show", lambda self: shown.append(self.windowTitle()))
    ws.settings.set("last_run_version", "0.2.0")
    update_ui.show_post_update(ws, win, [])
    assert shown and "업데이트 완료" in shown[0] and ws.settings.get("last_run_version") == update_ui.APP_VERSION
    assert not win._post_update_dialog.isModal()
    update_ui.show_post_update(ws, win, [])
    assert len(shown) == 1  # 한 번만


def test_update_offer_from_source_run(qapp, win, ws, monkeypatch):
    from crs import updater
    from crs.ui import update_ui
    info = updater.ReleaseInfo("9.0.0", "v9.0.0", "notes", "https://example/releases", zip_url="https://dl/zip")
    seen = {}

    def fake_open(self):
        seen["dlg"] = self
        self.choice = update_ui.UpdateDialog.SKIP
        self.finished.emit(1)

    monkeypatch.setattr(update_ui.UpdateDialog, "open", fake_open)
    win.updates.offer(info)
    assert ws.settings.get("skipped_version") == "9.0.0" and seen["dlg"]


def test_watchdog_records_gui_stall(qapp, tmp_path):
    from crs.watchdog import GuiWatchdog
    wd = GuiWatchdog(tmp_path, stall_sec=1.0)
    qapp.processEvents()
    time.sleep(4.5)            # 메인 스레드를 막아 멈춤을 흉내낸다
    end = time.time() + 2
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.05)
    wd.stop()
    text = (tmp_path / "hang.log").read_text(encoding="utf-8")
    assert "응답하지 않음" in text and "test_watchdog_records_gui_stall" in text and "응답 회복" in text


def test_image_view_fit_is_cheap_and_not_reentrant(qapp, tmp_path):
    """v0.3.4: 투명 결과 이미지 맞춤에서 외곽선 계산·스크롤바 반복이 일어나지 않아야 한다."""
    from PySide6.QtWidgets import QGraphicsPixmapItem
    from crs.ui.image_view import ImageView
    p = tmp_path / "t.png"
    im = Image.new("RGBA", (300, 500), (0, 0, 0, 0))
    for x in range(0, 300, 3):
        for y in range(0, 500, 5):
            im.putpixel((x, y), (255, 0, 0, 40))
    im.save(p)
    v = ImageView()
    assert v.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert v.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    v.resize(400, 300)
    v.show()
    v.set_image(p)
    qapp.processEvents()
    assert v._item.shapeMode() == QGraphicsPixmapItem.ShapeMode.BoundingRectShape
    calls = {"n": 0}
    orig = v.fitInView

    def counting(*a):
        calls["n"] += 1
        return orig(*a)

    v.fitInView = counting
    for _ in range(5):
        v.resize(400, 300)          # 같은 크기 → 다시 맞추지 않음
        qapp.processEvents()
    assert calls["n"] == 0
    v.resize(420, 320)
    qapp.processEvents()
    assert calls["n"] == 1
    scale = v.transform().m11()
    assert 0.5 < scale * 500 / v.viewport().height() < 1.01   # 세로가 화면에 맞음
    v.close()


def test_generate_sends_negative_last(qapp, win, ws, sample_png):
    """Positive → 화풍 → Negative('피할 것') 순서로 API 에 보낸다."""
    import re as _re
    seen = []

    def handler(request):
        seen.append(request.content)
        return ok_response()
    use_mock_api(ws, handler)
    setup_project(win, ws, sample_png)
    ws.save_default_prompt("front_view", "POSITIVE PART\n\n### Negative\nblurry eyes, extra ears")
    sid = ws.db.add_style("내 화풍", "SOFT CEL SHADING", "", None)
    win.gen.reload_styles(sid)
    win.gen.generate()
    wait_idle(qapp, win)
    body = seen[-1].decode("utf-8", "replace")
    m = _re.search(r'name="prompt"\r\n\r\n(.*?)\r\n--', body, _re.S)
    prompt = m.group(1)
    assert prompt.startswith("POSITIVE PART") and "### Negative" not in prompt
    assert prompt.index("SOFT CEL SHADING") < prompt.index("must NOT") and prompt.endswith("blurry eyes, extra ears")

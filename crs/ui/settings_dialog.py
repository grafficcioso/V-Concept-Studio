"""설정: API 키 · 프롬프트/프리셋 · 모델/요금표 · 예산 · 저장 위치."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, keystore
from ..config import default_prompt_text
from ..functions import FUNCTIONS
from ..pricing import GENERIC_MODEL
from ..services.image_service import GenerationError
from .workers import FuncWorker

TAB_KEYS = ["api", "prompts", "styles", "models", "budget", "google", "storage", "update"]
COLS = ["모델 ID", "표시 이름", "품질 (쉼표)", "크기 (쉼표)", "배경 (쉼표)", "임의 크기", "입력 충실도", "구형",
        "텍스트 입력 $/1M", "이미지 입력 $/1M", "이미지 출력 $/1M", "메모", "영역 지정(markup)"]


class SettingsDialog(QDialog):
    def __init__(self, ws, parent=None, open_tab="api"):
        super().__init__(parent)
        self.ws = ws
        self.restart_required = False
        self._worker = None
        self._loaded_prompt = ""
        self.setWindowTitle("설정")
        self.resize(1180, 660)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._api_tab(), "API")
        self.tabs.addTab(self._prompts_tab(), "프롬프트 · 프리셋")
        from .style_ui import StylesManager
        self.styles = StylesManager(ws)
        self.tabs.addTab(self.styles, "화풍 프리셋")
        self.tabs.addTab(self._models_tab(), "모델 · 요금표")
        self.tabs.addTab(self._budget_tab(), "예산 · 충전액")
        from .sync_ui import SyncSettingsWidget
        self.google = SyncSettingsWidget(ws, getattr(parent, "sync", None))
        self.tabs.addTab(self.google, "구글 드라이브")
        self.tabs.addTab(self._storage_tab(), "저장 위치")
        self.tabs.addTab(self._update_tab(), "업데이트")
        self.update_requested = None
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장 후 닫기")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("닫기")
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(buttons)
        key, _, arg = open_tab.partition(":")
        if key in TAB_KEYS:
            self.tabs.setCurrentIndex(TAB_KEYS.index(key))
        if key == "prompts" and arg:
            idx = self.fn_combo.findData(arg)
            if idx >= 0:
                self.fn_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------------ API
    def _api_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel(
            "OpenAI API 키는 사용자가 직접 발급해 등록합니다 (platform.openai.com → API keys).\n"
            "이미지 생성은 OpenAI 계정에 사용량만큼 과금되는 유료 기능입니다. 일부 모델은 조직 인증이 필요할 수 있습니다.\n"
            "키는 아래 보안 저장소에만 보관되며 설정 파일·DB·로그·프로젝트 파일에는 저장되지 않습니다.")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        self.key_backend = QLabel(keystore.backend_description())
        self.key_state = QLabel()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-… (새 키를 붙여넣고 [키 저장])")
        form.addRow("저장 방식", self.key_backend)
        form.addRow("현재 키", self.key_state)
        form.addRow("새 키", self.key_edit)
        lay.addLayout(form)
        row = QHBoxLayout()
        for text, fn in (("키 저장", self._save_key), ("키 삭제", self._delete_key),
                         ("연결 테스트 (무료 조회)", self._test_key)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        lay.addWidget(self.test_result)
        form2 = QFormLayout()
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 1200)
        self.timeout.setSuffix(" 초")
        self.timeout.setValue(int(self.ws.settings.get("timeout_sec")))
        form2.addRow("요청 타임아웃", self.timeout)
        self.demo = QCheckBox("데모 모드 — API를 호출하지 않고 화면 흐름만 확인 (비용 없음, 결과는 가짜 이미지)")
        self.demo.setChecked(self.ws.demo_mode)
        form2.addRow("", self.demo)
        lay.addLayout(form2)
        lay.addStretch(1)
        self._refresh_key_state()
        return w

    def _refresh_key_state(self):
        key = keystore.get_key()
        self.key_state.setText(f"등록됨 ({keystore.mask(key)})" if key else "등록된 키 없음")

    def _save_key(self):
        value = self.key_edit.text().strip()
        if not value:
            QMessageBox.information(self, "API 키", "새 키를 입력하세요.")
            return
        persistent = keystore.set_key(value)
        self.key_edit.clear()
        self._refresh_key_state()
        if not persistent:
            QMessageBox.information(self, "API 키", "이 PC에서는 보안 저장소를 사용할 수 없어 이번 실행 동안만 키를 기억합니다.\n"
                                    "앱을 다시 시작하면 키를 다시 입력해야 합니다. (디스크에는 저장하지 않았습니다.)")

    def _delete_key(self):
        if QMessageBox.question(self, "API 키", "저장된 API 키를 삭제할까요?") == QMessageBox.StandardButton.Yes:
            keystore.delete_key()
            self._refresh_key_state()
            self.test_result.setText("")

    def _run_bg(self, fn, on_done, on_error):
        if self._worker is not None:
            return False
        self._worker = FuncWorker(fn, self)
        self._worker.done.connect(on_done)
        self._worker.error.connect(on_error)
        self._worker.finished.connect(lambda: setattr(self, "_worker", None))
        self._worker.start()
        return True

    def _real_service(self):
        return self.ws._real_service

    def _test_key(self):
        if not keystore.get_key():
            self.test_result.setText("등록된 키가 없습니다.")
            return
        self.test_result.setText("확인 중…")

        def ok(ids):
            self.test_result.setText(f"✔ 연결 성공. 이 키로 조회되는 이미지 모델 {len(ids)}개: {', '.join(ids) or '(없음)'}\n"
                                     "(모델 조회는 과금되지 않습니다. 실제 생성 가능 여부는 계정 상태·조직 인증에 따라 다를 수 있습니다.)")

        def fail(e):
            msg = e.message if isinstance(e, GenerationError) else f"오류: {type(e).__name__}"
            self.test_result.setText("✖ " + msg)

        self._run_bg(self._real_service().list_image_models, ok, fail)

    # -------------------------------------------------------------- prompts
    def _prompts_tab(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        self.fn_combo = QComboBox()
        for f in FUNCTIONS:
            self.fn_combo.addItem(f.label, f.key)
        self.fn_combo.currentIndexChanged.connect(lambda *_: self._reload_preset_list())
        left.addWidget(QLabel("기능"))
        left.addWidget(self.fn_combo)
        self.fn_desc = QLabel()
        self.fn_desc.setWordWrap(True)
        self.fn_desc.setStyleSheet("color: #555;")
        left.addWidget(self.fn_desc)
        left.addWidget(QLabel("프리셋"))
        self.preset_list = QListWidget()
        self.preset_list.currentItemChanged.connect(self._on_preset_selected)
        left.addWidget(self.preset_list, 1)
        lay.addLayout(left, 1)

        right = QVBoxLayout()
        form = QFormLayout()
        self.preset_name = QLineEdit()
        form.addRow("이름", self.preset_name)
        self.preset_meta = QLabel()
        self.preset_meta.setWordWrap(True)
        form.addRow("연결 설정", self.preset_meta)
        right.addLayout(form)
        right.addWidget(QLabel("<b>Positive</b> — 만들고 싶은 것 (영문)"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("예: redraw the same character in a straight-on front view…")
        right.addWidget(self.prompt_edit, 3)
        right.addWidget(QLabel("<b>Negative</b> — 나오면 안 되는 것 (영문, 쉼표로 구분)"))
        self.neg_edit = QPlainTextEdit()
        self.neg_edit.setPlaceholderText("예: 3/4 view, tilted head, cropped hair, new accessories")
        right.addWidget(self.neg_edit, 1)
        row = QHBoxLayout()
        for text, fn in (("저장", self._save_prompt), ("복제…", self._duplicate_prompt), ("삭제", self._delete_preset),
                         ("기본값으로 초기화", self._reset_prompt), ("파일에서 불러오기…", self._import_prompt),
                         ("파일로 내보내기…", self._export_prompt)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        right.addLayout(row)
        tip = QLabel("저장한 프롬프트는 다음 생성 요청부터 적용됩니다. Negative 는 보낼 때 프롬프트 맨 끝에 "
                     "'다음은 나오면 안 됨' 문단으로 붙습니다(이미지 API 에 별도 negative 칸이 없기 때문). "
                     "'기본'은 prompts 폴더의 .md 파일(### Negative 줄 아래가 Negative)이며, "
                     "모델·품질·크기까지 묶어 두려면 생성 화면의 [현재 설정을 프리셋으로…]를 사용하세요.")
        tip.setWordWrap(True)
        right.addWidget(tip)
        lay.addLayout(right, 3)
        self._reload_preset_list()
        return w

    def _fn_key(self) -> str:
        return self.fn_combo.currentData()

    def _current_preset_id(self):
        item = self.preset_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _reload_preset_list(self, select_id=None):
        from ..functions import BY_KEY
        self.fn_desc.setText(BY_KEY[self._fn_key()].description)
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        base = QListWidgetItem(f"기본 (prompts/{self._fn_key()}.md)")
        base.setData(Qt.ItemDataRole.UserRole, None)
        self.preset_list.addItem(base)
        target = 0
        for i, p in enumerate(self.ws.db.list_presets(self._fn_key()), start=1):
            item = QListWidgetItem(p["name"])
            item.setData(Qt.ItemDataRole.UserRole, p["id"])
            self.preset_list.addItem(item)
            if p["id"] == select_id:
                target = i
        self.preset_list.blockSignals(False)
        self.preset_list.setCurrentRow(target)
        self._on_preset_selected()

    def _on_preset_selected(self, *_):
        pid = self._current_preset_id()
        p = self.ws.db.get_preset(pid)
        if p:
            self.preset_name.setText(p["name"])
            self.preset_name.setEnabled(True)
            parts = [f"{k}: {p[c]}" for k, c in (("모델", "model"), ("품질", "quality"), ("크기", "size"),
                                                  ("배경", "background")) if p[c]]
            self.preset_meta.setText(" · ".join(parts) or "프롬프트만 (모델·옵션은 현재 선택 유지)")
            text = p["prompt"]
        else:
            self.preset_name.setText("기본")
            self.preset_name.setEnabled(False)
            self.preset_meta.setText(str(self.ws.prompt_file(self._fn_key())))
            text = self.ws.default_prompt(self._fn_key())
        self._loaded_prompt = text
        self._set_prompt_text(text)

    def _set_prompt_text(self, text: str):
        from ..functions import split_prompt
        pos, neg = split_prompt(text)
        self.prompt_edit.setPlainText(pos)
        self.neg_edit.setPlainText(neg)

    def _prompt_text(self) -> str:
        from ..functions import join_prompt
        return join_prompt(self.prompt_edit.toPlainText(), self.neg_edit.toPlainText())

    def _save_prompt(self):
        text = self._prompt_text()
        if not self.prompt_edit.toPlainText().strip():
            QMessageBox.warning(self, "프롬프트", "Positive 프롬프트가 비어 있습니다.")
            return
        pid = self._current_preset_id()
        if pid:
            name = self.preset_name.text().strip() or "이름 없음"
            self.ws.db.update_preset(pid, name=name, prompt=text)
            self._reload_preset_list(pid)
        else:
            self.ws.save_default_prompt(self._fn_key(), text)
            self._loaded_prompt = text
        QMessageBox.information(self, "프롬프트", "저장했습니다. 다음 요청부터 적용됩니다.")

    def _duplicate_prompt(self):
        name, ok = QInputDialog.getText(self, "프리셋 복제", "새 프리셋 이름:", text=self.preset_name.text() + " 사본")
        if not ok or not name.strip():
            return
        src = self.ws.db.get_preset(self._current_preset_id()) or {}
        new_id = self.ws.db.add_preset(name.strip(), self._fn_key(), self._prompt_text(),
                                       src.get("model"), src.get("quality"), src.get("size"), src.get("background"))
        self._reload_preset_list(new_id)

    def _delete_preset(self):
        pid = self._current_preset_id()
        if not pid:
            QMessageBox.information(self, "프리셋", "'기본'은 삭제할 수 없습니다. [기본값으로 초기화]를 사용하세요.")
            return
        if QMessageBox.question(self, "프리셋", "이 프리셋을 삭제할까요? (이미 생성된 결과의 기록은 유지됩니다)") \
                == QMessageBox.StandardButton.Yes:
            self.ws.db.delete_preset(pid)
            self._reload_preset_list()

    def _reset_prompt(self):
        if QMessageBox.question(self, "초기화", "이 기능의 내장 기본 프롬프트로 되돌릴까요?") != QMessageBox.StandardButton.Yes:
            return
        text = default_prompt_text(self._fn_key())
        pid = self._current_preset_id()
        if pid:
            self.ws.db.update_preset(pid, prompt=text)
        else:
            self.ws.reset_default_prompt(self._fn_key())
        self._loaded_prompt = text
        self._set_prompt_text(text)

    def _import_prompt(self):
        f, _ = QFileDialog.getOpenFileName(self, "프롬프트 불러오기", "", "텍스트 (*.md *.txt);;모든 파일 (*)")
        if f:
            try:
                self._set_prompt_text(Path(f).read_text(encoding="utf-8").strip())
            except (OSError, UnicodeDecodeError) as e:
                QMessageBox.warning(self, "불러오기", f"파일을 읽지 못했습니다: {e}")

    def _export_prompt(self):
        f, _ = QFileDialog.getSaveFileName(self, "프롬프트 내보내기", f"{self._fn_key()}.md", "Markdown (*.md)")
        if f:
            try:
                Path(f).write_text(self._prompt_text() + "\n", encoding="utf-8")
            except OSError as e:
                QMessageBox.warning(self, "내보내기", f"저장하지 못했습니다: {e}")

    # --------------------------------------------------------------- models
    def _models_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        info = QLabel("모델명·옵션·가격은 여기서 직접 관리합니다 (자동 업데이트 없음). OpenAI 공식 가격표와 대조한 뒤 "
                      "'최종 확인일'을 갱신하세요. 여기 없는 옵션은 생성 화면에서 선택할 수 없습니다.\n"
                      "품질·크기별 장당 참고 단가(reference_per_image_usd)는 JSON 파일에서 편집할 수 있습니다.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        row.addWidget(QLabel("요금표 최종 확인일"))
        self.verified = QLineEdit(self.ws.catalog.data.get("verified_date", ""))
        self.verified.setFixedWidth(120)
        row.addWidget(self.verified)
        b = QPushButton("오늘로")
        b.clicked.connect(lambda: self.verified.setText(date.today().isoformat()))
        row.addWidget(b)
        row.addStretch(1)
        for text, fn in (("행 추가", self._add_model_row), ("선택 행 삭제", self._del_model_row),
                         ("서버에서 모델 목록 가져오기", self._fetch_models), ("기본값 복원", self._reset_models),
                         ("JSON 파일 위치 열기", lambda: QDesktopServices.openUrl(
                             QUrl.fromLocalFile(str(self.ws.paths.models_file.parent))))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.models_msg = QLabel()
        self.models_msg.setWordWrap(True)
        lay.addWidget(self.models_msg)
        row = QHBoxLayout()
        row.addWidget(QLabel("화풍 추출용 텍스트 모델"))
        self.style_model = QComboBox()
        self.style_model.setEditable(True)
        self.style_model.addItems(self.ws.catalog.text_model_ids())
        self.style_model.setCurrentText(self.ws.style_model())
        self.style_model.currentTextChanged.connect(self._on_style_model)
        row.addWidget(self.style_model, 1)
        self.style_model_info = QLabel()
        row.addWidget(self.style_model_info, 2)
        lay.addLayout(row)
        self._on_style_model(self.style_model.currentText())
        self._load_models_table()
        return w

    def _on_style_model(self, mid):
        tm = self.ws.catalog.text_model(mid.strip())
        p = tm.get("pricing") or {}
        if "input" in p:
            self.style_model_info.setText(f"입력 ${p['input']:g} / 출력 ${p['output']:g} (1M 토큰당) · {tm.get('note', '')}")
        else:
            self.style_model_info.setText("요금표에 없는 모델 — 비용 계산 불가. JSON 파일의 text_models 에 추가할 수 있습니다.")
        self.style_model_info.setWordWrap(True)

    def _load_models_table(self):
        self.table.setRowCount(0)
        for m in self.ws.catalog.data["models"]:
            self._append_model_row({**GENERIC_MODEL, **m})
        self.table.resizeColumnsToContents()

    def _append_model_row(self, m: dict):
        r = self.table.rowCount()
        self.table.insertRow(r)
        p = m.get("pricing") or {}
        texts = {0: m.get("id", ""), 1: m.get("label", ""), 2: ", ".join(m["qualities"]), 3: ", ".join(m["sizes"]),
                 4: ", ".join(m["backgrounds"]), 8: _num(p.get("text_input")), 9: _num(p.get("image_input")),
                 10: _num(p.get("image_output")), 11: m.get("note", "")}
        checks = {5: m.get("custom_size"), 6: m.get("input_fidelity"), 7: m.get("deprecated"), 12: m.get("markup")}
        for c in range(len(COLS)):
            item = QTableWidgetItem(texts.get(c, ""))
            if c in checks:
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(Qt.CheckState.Checked if checks[c] else Qt.CheckState.Unchecked)
            self.table.setItem(r, c, item)

    def _add_model_row(self):
        self._append_model_row({**GENERIC_MODEL, "id": "", "label": "", "note": ""})
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _del_model_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)

    def _reset_models(self):
        if QMessageBox.question(self, "요금표", "앱에 내장된 기본 모델·요금표로 되돌릴까요? 직접 수정한 내용은 사라집니다.") \
                == QMessageBox.StandardButton.Yes:
            self.ws.catalog.reset_to_default()
            self.verified.setText(self.ws.catalog.data.get("verified_date", ""))
            self._load_models_table()

    def _fetch_models(self):
        if not keystore.get_key():
            self.models_msg.setText("API 키가 필요합니다.")
            return
        self.models_msg.setText("서버에서 모델 목록을 가져오는 중…")

        def ok(ids):
            known = {self.table.item(r, 0).text().strip() for r in range(self.table.rowCount())}
            new = [i for i in ids if i not in known]
            for mid in new:
                self._append_model_row({**GENERIC_MODEL, "id": mid, "label": mid,
                                        "note": "서버 목록에서 추가됨 — 옵션/가격을 확인해 입력하세요."})
            missing = sorted(known - set(ids) - {""})
            msg = f"계정에서 조회된 이미지 모델: {', '.join(ids) or '(없음)'}.\n새로 추가한 행: {', '.join(new) or '없음'}."
            if missing:
                msg += f"\n요금표에는 있지만 이 계정에서 조회되지 않는 모델: {', '.join(missing)} (사용 불가일 수 있음)"
            self.models_msg.setText(msg + "\n새 모델의 가격은 임의로 채우지 않았습니다. 공식 가격표를 확인해 입력하세요.")

        def fail(e):
            self.models_msg.setText("✖ " + (e.message if isinstance(e, GenerationError) else type(e).__name__))

        self._run_bg(self._real_service().list_image_models, ok, fail)

    def _collect_models(self) -> list[dict]:
        old = {m.get("id"): m for m in self.ws.catalog.data["models"]}
        out = []
        for r in range(self.table.rowCount()):
            t = lambda c: (self.table.item(r, c).text() if self.table.item(r, c) else "").strip()
            chk = lambda c: self.table.item(r, c).checkState() == Qt.CheckState.Checked
            mid = t(0)
            if not mid:
                continue
            lst = lambda c, default: [x.strip().lower() for x in t(c).split(",") if x.strip()] or default
            pricing = {}
            for key, c in (("text_input", 8), ("image_input", 9), ("image_output", 10)):
                try:
                    if t(c):
                        pricing[key] = float(t(c))
                except ValueError:
                    raise ValueError(f"{mid}: '{COLS[c]}' 값이 숫자가 아닙니다.")
            m = {"id": mid, "label": t(1) or mid, "qualities": lst(2, ["auto"]), "sizes": lst(3, ["auto"]),
                 "backgrounds": lst(4, ["auto"]), "custom_size": chk(5), "input_fidelity": chk(6), "markup": chk(12),
                 "pricing": pricing, "reference_per_image_usd": old.get(mid, {}).get("reference_per_image_usd", {}),
                 "note": t(11)}
            if chk(7):
                m["deprecated"] = True
            out.append(m)
        return out

    # --------------------------------------------------------------- budget
    def _budget_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        form = QFormLayout()
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0, 100000)
        self.budget.setDecimals(2)
        self.budget.setPrefix("$ ")
        self.budget.setSpecialValueText("사용 안 함")
        self.budget.setValue(float(self.ws.settings.get("monthly_budget_usd") or 0))
        form.addRow("앱 내부 월간 소프트 예산", self.budget)
        self.confirm = QCheckBox("생성 요청을 보내기 전에 항상 예상 비용 확인 창 표시")
        self.confirm.setChecked(bool(self.ws.settings.get("confirm_before_request")))
        form.addRow("", self.confirm)
        self.credit = QDoubleSpinBox()
        self.credit.setRange(0, 100000)
        self.credit.setDecimals(2)
        self.credit.setPrefix("$ ")
        self.credit.setSpecialValueText("미설정")
        self.credit.setValue(float(self.ws.settings.get("credit_total_usd") or 0))
        form.addRow("충전한 금액", self.credit)
        self.credit_since = QDateEdit()
        self.credit_since.setCalendarPopup(True)
        self.credit_since.setDisplayFormat("yyyy-MM-dd")
        from PySide6.QtCore import QDate
        since = self.ws.settings.get("credit_since") or ""
        self.credit_since.setDate(QDate.fromString(since, "yyyy-MM-dd") if since else QDate.currentDate())
        form.addRow("충전 기준일", self.credit_since)
        row = QHBoxLayout()
        b = QPushButton("OpenAI 결제(잔액) 페이지 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://platform.openai.com/settings/organization/billing/overview")))
        row.addWidget(b)
        b = QPushButton("사용량 페이지 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://platform.openai.com/usage")))
        row.addWidget(b)
        row.addStretch(1)
        form.addRow("", row)
        lay.addLayout(form)
        credit_note = QLabel(
            "· 충전액을 입력하면 상단에 '충전 − 앱 기록 사용액 = 추정 잔액'을 보여 줍니다. 기준일 이후의 이 앱 사용분만 뺍니다.\n"
            "· OpenAI는 잔액 조회용 공식 API를 제공하지 않아 자동으로 가져올 수 없습니다. 충전할 때마다 금액과 날짜를 맞춰 주세요.\n"
            "  (예: 남은 잔액 $3.20 에서 $5 를 더 충전했다면 충전액 $8.20, 기준일 오늘)")
        credit_note.setWordWrap(True)
        lay.addWidget(credit_note)
        note = QLabel(
            "· 월간 예산은 이 앱이 기록한 '로컬 추정 비용'의 합계를 기준으로 경고만 하는 소프트 제한입니다.\n"
            "· OpenAI 서버의 청구를 차단하지 않으며, 다른 앱/기기에서 같은 계정으로 쓴 금액은 포함되지 않습니다.\n"
            "· 실제 지출 한도는 OpenAI 대시보드(Billing → Limits)에서 설정하세요.\n"
            "· 예산을 넘는 요청은 위 옵션과 관계없이 항상 확인 창이 뜹니다.\n"
            "· 네트워크 오류 등으로 과금 여부를 알 수 없는 요청은 합계에 넣지 않고 '불명'으로 따로 셉니다.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    # -------------------------------------------------------------- storage
    def _storage_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("프로젝트·라이브러리·프롬프트·DB·로그가 저장되는 데이터 폴더:"))
        self.root_label = QLineEdit(str(self.ws.paths.root))
        self.root_label.setReadOnly(True)
        lay.addWidget(self.root_label)
        row = QHBoxLayout()
        b = QPushButton("폴더 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ws.paths.root))))
        row.addWidget(b)
        b = QPushButton("위치 변경…")
        b.clicked.connect(self._change_root)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        note = QLabel("위치를 바꾸면 기존 데이터를 새 폴더로 복사할 수 있습니다 (기존 폴더는 삭제하지 않습니다). "
                      "변경 후에는 앱을 다시 시작해야 합니다.\nDB에는 상대 경로만 저장되므로 폴더째 옮겨도 연결이 유지됩니다.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _change_root(self):
        d = QFileDialog.getExistingDirectory(self, "새 데이터 폴더 선택", str(self.ws.paths.root.parent))
        if not d:
            return
        new_root = Path(d)
        if new_root.resolve() == self.ws.paths.root.resolve():
            return
        if self.ws.paths.root.resolve() in new_root.resolve().parents:
            QMessageBox.warning(self, "저장 위치", "현재 데이터 폴더 안쪽은 선택할 수 없습니다.")
            return
        has_data = (new_root / "data" / "app.db").exists()
        if has_data:
            q = "선택한 폴더에 이미 앱 데이터가 있습니다. 그 데이터를 그대로 사용할까요?\n(현재 데이터는 복사하지 않습니다.)"
            if QMessageBox.question(self, "저장 위치", q) != QMessageBox.StandardButton.Yes:
                return
        else:
            if any(new_root.iterdir()):
                new_root = new_root / APP_NAME
            ans = QMessageBox.question(self, "저장 위치", f"새 위치: {new_root}\n\n현재 데이터를 새 위치로 복사할까요?\n"
                                       "(아니요 = 빈 상태로 시작)",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                       QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            try:
                if ans == QMessageBox.StandardButton.Yes:
                    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    try:
                        self.ws.copy_data_to(new_root)
                    finally:
                        QApplication.restoreOverrideCursor()
                else:
                    new_root.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                QMessageBox.warning(self, "저장 위치", f"복사하지 못했습니다: {e}")
                return
        self.ws.settings.set("data_root", str(new_root))
        self.ws.settings.set("last_project_id", None)
        self.restart_required = True
        QMessageBox.information(self, "저장 위치", "저장 위치를 변경했습니다. 앱을 종료합니다. 다시 실행해 주세요.")
        self.accept()

    # --------------------------------------------------------------- update
    def _update_tab(self) -> QWidget:
        from .. import APP_VERSION, GITHUB_REPO
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(f"현재 버전: v{APP_VERSION}"))
        self.check_updates = QCheckBox("프로그램을 켤 때 새 버전이 있는지 확인 (GitHub 릴리스 조회, 무료)")
        self.check_updates.setChecked(bool(self.ws.settings.get("check_updates")))
        lay.addWidget(self.check_updates)
        row = QHBoxLayout()
        b = QPushButton("지금 업데이트 확인")
        b.clicked.connect(self._check_now)
        row.addWidget(b)
        b = QPushButton("릴리스 페이지 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://github.com/{GITHUB_REPO}/releases")))
        row.addWidget(b)
        b = QPushButton("변경 내역 보기")
        b.clicked.connect(self._show_changelog)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.update_msg = QLabel()
        self.update_msg.setWordWrap(True)
        lay.addWidget(self.update_msg)
        note = QLabel("· 업데이트 파일은 GitHub 릴리스(github.com/" + GITHUB_REPO + ")에서 받습니다.\n"
                      "· 업데이트해도 프로젝트·라이브러리·프롬프트·API 키는 그대로 유지됩니다(프로그램 폴더 밖에 저장).\n"
                      "· 프로그램 폴더가 쓰기 금지 위치(예: Program Files)면 자동 설치가 안 되고 다운로드 페이지를 안내합니다.")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        return w

    def _check_now(self):
        from ..updater import check_latest
        self.update_msg.setText("확인 중…")

        def ok(info):
            if info is None:
                self.update_msg.setText("✔ 최신 버전을 사용 중입니다.")
            else:
                self.update_msg.setText(f"새 버전 v{info.version} 이 있습니다. 창을 닫으면 업데이트 안내가 뜹니다.")
                self.update_requested = info

        def fail(e):
            self.update_msg.setText(f"✖ 확인하지 못했습니다: {e}")

        self._run_bg(lambda: check_latest(skipped=""), ok, fail)

    def _show_changelog(self):
        from .update_ui import ChangelogDialog
        ChangelogDialog(self).exec()

    # ----------------------------------------------------------------- save
    def _save_and_close(self):
        from ..functions import join_prompt, split_prompt
        if self._prompt_text() != join_prompt(*split_prompt(self._loaded_prompt)):
            ans = QMessageBox.question(self, "프롬프트", "편집 중인 프롬프트가 저장되지 않았습니다. 저장할까요?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                                       QMessageBox.StandardButton.Cancel)
            if ans == QMessageBox.StandardButton.Cancel:
                return
            if ans == QMessageBox.StandardButton.Yes:
                self._save_prompt()
        try:
            models = self._collect_models()
        except ValueError as e:
            QMessageBox.warning(self, "모델 · 요금표", str(e))
            self.tabs.setCurrentIndex(TAB_KEYS.index("models"))
            return
        ids = [m["id"] for m in models]
        if len(ids) != len(set(ids)):
            QMessageBox.warning(self, "모델 · 요금표", "모델 ID가 중복되었습니다.")
            return
        cat = self.ws.catalog
        cat.data["models"] = models
        cat.data["verified_date"] = self.verified.text().strip()
        cat.save()
        s = self.ws.settings
        s.set("monthly_budget_usd", float(self.budget.value()), save=False)
        s.set("confirm_before_request", self.confirm.isChecked(), save=False)
        s.set("timeout_sec", int(self.timeout.value()), save=False)
        s.set("credit_total_usd", float(self.credit.value()), save=False)
        s.set("credit_since", self.credit_since.date().toString("yyyy-MM-dd"), save=False)
        s.set("check_updates", self.check_updates.isChecked(), save=False)
        sm = self.style_model.currentText().strip()
        s.set("style_model", "" if sm == (self.ws.catalog.text_model_ids() or [""])[0] else sm, save=False)
        s.set("demo_mode", self.demo.isChecked())
        self.accept()

    def done(self, code):
        if self._worker is not None:
            self._worker.wait(35000)
        self.google.wait(1000)
        super().done(code)


def _num(v) -> str:
    return "" if v is None else f"{v:g}"

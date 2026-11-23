"""생성 화면: 원본/결과 비교, 생성 패널, 생성 기록."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from .. import keystore
from ..functions import BY_KEY, FUNCTIONS, label_of
from ..workspace import SUPPORTED_EXT
from ..functions import PART_TYPES, compose_prompt
from .dialogs import (AssetDialog, CropDialog, MetaDialog, add_thumb_item, copy_image_to_clipboard, make_thumb_list,
                      qimage_to_pil)
from .style_ui import StyleController
from .gen_controller import GenerationController, GenJob
from .image_view import ImagePanel

IMAGE_FILTER = "이미지 (*.png *.jpg *.jpeg *.webp)"


class GenerateTab(QWidget):
    openSettings = Signal(str)
    syncRequested = Signal()
    itemsEdited = Signal()
    assetsChanged = Signal()
    projectChanged = Signal()

    def __init__(self, ws, controller: GenerationController, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.ctl = controller
        self.project_id: int | None = None
        self.input_id: int | None = None
        self.result_id: int | None = None
        self.function_key = FUNCTIONS[0].key
        self._retouch_states: dict[int, dict] = {}
        self._pending_retouch: tuple[int, dict] | None = None
        self._part_states: dict[int, dict] = {}
        self._pending_set: dict | None = None   # v0.6.0 헤드+헤어 세트 2단계 대기
        self._batch: dict | None = None         # v0.6.0 일괄 생성 (핀터레스트 가져오기 후)
        self._started_at = 0.0
        self._loading = False
        self.style_ctl = StyleController(ws, self)
        self.style_ctl.stylesChanged.connect(lambda sid: self.reload_styles(sid))
        self._build()
        self.ctl.started.connect(self._on_started)
        self.ctl.finished.connect(self._on_finished)
        self.ctl.failed.connect(self._on_failed)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self.paste)
        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self.copy_current)
        self.reload_models()
        self.reload_styles(self.ws.settings.get("last_style_id"))
        self.reload_projects(self.ws.settings.get("last_project_id"))
        self._select_function(self.function_key)
        self.refresh_api_status()

    # =====================================================================
    # UI 구성
    # =====================================================================
    def _build(self):
        root = QVBoxLayout(self)

        # ---- 상단 바 ----
        top = QHBoxLayout()
        top.addWidget(QLabel("프로젝트"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(200)
        self.project_combo.currentIndexChanged.connect(self._on_project_combo)
        top.addWidget(self.project_combo)
        for text, fn in (("새 프로젝트", self.new_project), ("원본 열기", self.open_files),
                         ("클립보드 붙여넣기 (Ctrl+V)", self.paste), ("핀터레스트…", self.open_pinterest)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            top.addWidget(b)
        top.addStretch(1)
        self.sync_btn = QPushButton("☁")
        self.sync_btn.setObjectName("sync_btn")
        self.sync_btn.setFlat(True)
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.clicked.connect(self.syncRequested.emit)
        top.addWidget(self.sync_btn)
        self.credit_btn = QPushButton()
        self.credit_btn.setObjectName("credit_btn")
        self.credit_btn.setFlat(True)
        self.credit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.credit_btn.clicked.connect(lambda: self.openSettings.emit("budget"))
        top.addWidget(self.credit_btn)
        self.api_status = QLabel()
        self.api_status.setObjectName("api_status")
        top.addWidget(self.api_status)
        b = QPushButton("설정")
        b.clicked.connect(lambda: self.openSettings.emit("api"))
        top.addWidget(b)
        root.addLayout(top)

        # ---- 중앙: 비교 미리보기 + 생성 패널 ----
        self.input_panel = ImagePanel("원본 (입력)", "여기에 이미지를 끌어다 놓거나\nCtrl+V 로 붙여넣으세요\n(PNG / JPG / WEBP)",
                                      accept_drops=True)
        self.input_panel.view.filesDropped.connect(self.import_files)
        self.input_panel.view.imageDropped.connect(self.import_qimage)
        self.input_panel.view.urlDropped.connect(self._on_url_drop)
        self.result_panel = ImagePanel("결과", "생성 결과가 여기에 표시됩니다")
        self.result_panel.setObjectName("result_panel")
        self.input_panel.setObjectName("input_panel")
        self.result_info = QLabel("")
        self.result_info.setWordWrap(True)
        self.result_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right_box = QWidget()
        rl = QVBoxLayout(right_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.result_panel, 1)
        rl.addWidget(self.result_info)
        compare = QSplitter(Qt.Orientation.Horizontal)
        compare.addWidget(self.input_panel)
        compare.addWidget(right_box)
        compare.setSizes([500, 500])

        center = QHBoxLayout()
        center.addWidget(compare, 1)
        center.addWidget(self._build_gen_panel())
        root.addLayout(center, 1)

        # ---- 하단: 원본 목록 + 생성 기록 ----
        bottom = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel("원본 목록 (클릭 = 입력으로 지정, 우클릭 = 메뉴)"))
        self.originals = make_thumb_list()
        self.originals.setObjectName("originals_list")
        self.originals.itemClicked.connect(lambda it: self.set_input(it.data(Qt.ItemDataRole.UserRole)))
        self.originals.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.originals.customContextMenuRequested.connect(self._originals_menu)
        left.addWidget(self.originals)
        left.addStretch(1)
        bottom.addLayout(left, 2)

        right = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(QLabel("생성 기록"))
        self.fav_only = QCheckBox("즐겨찾기만")
        self.fav_only.toggled.connect(self.reload_lists)
        head.addWidget(self.fav_only)
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름·태그 검색")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload_lists)
        head.addWidget(self.search, 1)
        right.addLayout(head)
        self.history = make_thumb_list()
        self.history.setObjectName("history_list")
        self.history.currentItemChanged.connect(self._on_history_item)
        right.addWidget(self.history)
        self.action_buttons = []
        rows = (
            (("PNG로 저장…", self.save_result_as, "선택한 결과를 원하는 위치에 PNG로 내보냅니다"),
             ("복사 (Ctrl+C)", self.copy_result, "선택한 결과를 클립보드에 복사합니다"),
             ("★ 즐겨찾기", self.toggle_favorite, "즐겨찾기 표시/해제"),
             ("이름·태그…", self.edit_meta, "이름, 태그, 메모 편집"),
             ("삭제", self.delete_result, "결과 삭제")),
            (("입력으로 사용 →", self.use_result_as_input, "이 결과를 다음 작업의 입력으로 사용"),
             ("✎ 부분 수정…", self.retouch_result, "빨간 펜으로 영역을 칠하고 수정 요청 (유료 생성)"),
             ("파트 사면도…", self.part_views_result, "칠한 파트(헤어/헤드 등)만 잘라 4방향 시트 생성 (유료 생성)"),
             ("화풍 추출…", self.extract_style_result, "이 이미지의 화풍을 분석해 화풍 프롬프트로 저장 (유료, 소액)"),
             ("에셋 등록…", self.register_asset, "결과 전체를 에셋 라이브러리에 등록"),
             ("잘라서 에셋 등록…", self.crop_asset, "시트에서 영역을 골라 개별 에셋으로 등록")),
        )
        actions = QVBoxLayout()
        for row_def in rows:
            row = QHBoxLayout()
            for text, fn, tip in row_def:
                b = QPushButton(text)
                b.setToolTip(tip)
                b.clicked.connect(fn)
                row.addWidget(b)
                self.action_buttons.append(b)
            actions.addLayout(row)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu)
        right.addLayout(actions)
        bottom.addLayout(right, 5)
        root.addLayout(bottom)

    def _build_gen_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)

        box = QGroupBox("1. 기능")
        grid = QGridLayout(box)
        self.fn_group = QButtonGroup(self)
        self.fn_group.setExclusive(True)
        self.fn_buttons = {}
        primary = [f for f in FUNCTIONS if f.show_in_panel and f.primary]
        extra = [f for f in FUNCTIONS if f.show_in_panel and not f.primary]
        for i, f in enumerate(primary):
            b = QPushButton(f.label)
            b.setCheckable(True)
            b.setMinimumHeight(38)
            b.setStyleSheet("font-weight: bold;")
            grid.addWidget(b, 0, i)
            self.fn_buttons[f.key] = b
        for i, f in enumerate(extra):
            b = QPushButton(f.label)
            b.setCheckable(True)
            grid.addWidget(b, 1, i)
            self.fn_buttons[f.key] = b
        for key, b in self.fn_buttons.items():
            b.setToolTip(BY_KEY[key].description)
            self.fn_group.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self._select_function(k))
        self.fn_desc = QLabel()
        self.fn_desc.setWordWrap(True)
        grid.addWidget(self.fn_desc, 2, 0, 1, max(3, len(primary)))
        lay.addWidget(box)

        box = QGroupBox("2. 프리셋 · 모델 · 옵션")
        form = QFormLayout(box)
        self.preset_combo = QComboBox()
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        form.addRow("프리셋", self.preset_combo)
        row = QHBoxLayout()
        b = QPushButton("현재 설정을 프리셋으로…")
        b.clicked.connect(self.save_as_preset)
        row.addWidget(b)
        b = QPushButton("프롬프트 편집…")
        b.clicked.connect(lambda: self.openSettings.emit("prompts:" + self.function_key))
        row.addWidget(b)
        form.addRow(row)
        self.style_combo = QComboBox()
        self.style_combo.setToolTip("고른 화풍 프롬프트가 모든 생성 요청의 프롬프트 뒤에 붙습니다.")
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        style_row = QHBoxLayout()
        style_row.addWidget(self.style_combo, 1)
        b = QPushButton("관리…")
        b.clicked.connect(lambda: self.openSettings.emit("styles"))
        style_row.addWidget(b)
        form.addRow("화풍", style_row)
        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        form.addRow("모델", self.model_combo)
        self.quality_combo = QComboBox()
        self.size_combo = QComboBox()
        self.bg_combo = QComboBox()
        self.fidelity_combo = QComboBox()
        self.fidelity_combo.addItem("모델 기본값", None)
        self.fidelity_combo.addItem("high (입력 보존 우선)", "high")
        self.fidelity_combo.addItem("low", "low")
        form.addRow("품질", self.quality_combo)
        form.addRow("크기", self.size_combo)
        form.addRow("배경", self.bg_combo)
        form.addRow("입력 충실도", self.fidelity_combo)
        for cb in (self.quality_combo, self.size_combo, self.bg_combo):
            cb.currentTextChanged.connect(self._on_option_changed)
        self.model_note = QLabel()
        self.model_note.setWordWrap(True)
        self.model_note.setStyleSheet("color: #666;")
        form.addRow(self.model_note)
        lay.addWidget(box)

        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # 비용·생성 영역은 스크롤 밖에 고정해 항상 보이게 한다.
        box = QGroupBox("3. 비용 · 생성")
        v = QVBoxLayout(box)
        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setMinimumHeight(38)
        self.estimate_label.setStyleSheet("font-weight: bold;")
        v.addWidget(self.estimate_label)
        self._estimate_detail = ""
        b = QPushButton("예상 비용 근거 · 주의사항 보기")
        b.setFlat(True)
        b.setStyleSheet("text-align: left; color: #0b57d0;")
        b.clicked.connect(lambda: QMessageBox.information(self, "예상 비용", self._estimate_detail or "-"))
        v.addWidget(b)
        self.generate_btn = QPushButton("생성")
        self.generate_btn.setMinimumHeight(44)
        self.generate_btn.setStyleSheet("font-weight: bold; font-size: 15px;")
        self.generate_btn.clicked.connect(self.generate)
        v.addWidget(self.generate_btn)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        v.addWidget(self.progress)
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        v.addWidget(self.progress_label)
        self.cancel_btn = QPushButton("요청 취소")
        self.cancel_btn.setToolTip("연결을 끊습니다. 서버에서 이미 처리가 시작됐다면 과금될 수 있습니다.")
        self.cancel_btn.clicked.connect(self.ctl.cancel)
        self.cancel_btn.hide()
        v.addWidget(self.cancel_btn)
        note = QLabel("AI 결과는 원본과 100% 동일하지 않을 수 있습니다. 실패 시 자동 재시도하지 않습니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        v.addWidget(note)

        outer = QWidget()
        outer.setFixedWidth(370)
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(scroll, 1)
        ol.addWidget(box)
        return outer

    # =====================================================================
    # 프로젝트 / 목록
    # =====================================================================
    def reload_projects(self, select_id=None):
        self._loading = True
        self.project_combo.clear()
        projects = self.ws.db.list_projects()
        for p in projects:
            self.project_combo.addItem(p["name"], p["id"])
        self._loading = False
        ids = [p["id"] for p in projects]
        if not ids:
            self.set_project(None)
            return
        idx = ids.index(select_id) if select_id in ids else len(ids) - 1
        self.project_combo.setCurrentIndex(idx)
        self.set_project(ids[idx])

    def _on_project_combo(self, idx):
        if not self._loading and idx >= 0:
            self.set_project(self.project_combo.itemData(idx))

    def set_project(self, pid):
        if pid == self.project_id and pid is not None:
            return
        self.project_id = pid
        self.input_id = None
        self.result_id = None
        if pid:
            self.ws.settings.set("last_project_id", pid)
        self.reload_lists()
        originals = self.ws.db.list_images(pid, "original") if pid else []
        self.set_input(originals[0]["id"] if originals else None)
        self.show_result(None)
        if self.history.count():
            self.history.setCurrentRow(0)
        self.projectChanged.emit()

    def new_project(self) -> bool:
        name, ok = QInputDialog.getText(self, "새 프로젝트", "프로젝트(캐릭터) 이름:")
        if not ok or not name.strip():
            return False
        try:
            pid = self.ws.create_project(name)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "프로젝트", str(e))
            return False
        self.reload_projects(pid)
        return True

    def _ensure_project(self) -> bool:
        if self.project_id:
            return True
        QMessageBox.information(self, "프로젝트", "먼저 프로젝트를 만들어 주세요.")
        return self.new_project()

    def reload_lists(self, *_):
        keep = self.result_id
        self.originals.clear()
        self.history.blockSignals(True)
        self.history.clear()
        if self.project_id:
            for row in self.ws.db.list_images(self.project_id, "original"):
                add_thumb_item(self.originals, row["name"] or f"원본 #{row['id']}",
                               self.ws.paths.abs(row["thumb_path"]), row["id"])
            rows = self.ws.db.list_images(self.project_id, "output", self.fav_only.isChecked(),
                                          self.search.text().strip())
            for row in rows:
                star = "★ " if row["favorite"] else ""
                text = star + (row["name"] or f"{label_of(row['function'])} {row['created_at'][11:16]}")
                tip = f"{label_of(row['function'])} · {row['model']} · {row['created_at']}\n태그: {row['tags'] or '-'}"
                item = add_thumb_item(self.history, text, self.ws.paths.abs(row["thumb_path"]), row["id"], tip)
                if row["id"] == keep:
                    self.history.setCurrentItem(item)
        self.history.blockSignals(False)

    def _on_history_item(self, item, _prev=None):
        self.show_result(item.data(Qt.ItemDataRole.UserRole) if item else None)

    # =====================================================================
    # 입력 / 결과 표시
    # =====================================================================
    def set_input(self, image_id):
        row = self.ws.db.get_image(image_id)
        self.input_id = row["id"] if row else None
        if row:
            kind = "원본" if row["kind"] == "original" else f"생성 결과({label_of(row['function'])})"
            self.input_panel.set_title(f"입력 — {kind}: {row['name'] or '#' + str(row['id'])}")
            self.input_panel.set_image(self.ws.image_path(row))
        else:
            self.input_panel.set_title("원본 (입력)")
            self.input_panel.set_image(None)
        self.update_estimate()

    def show_result(self, image_id):
        row = self.ws.db.get_image(image_id)
        self.result_id = row["id"] if row else None
        for b in self.action_buttons:
            b.setEnabled(row is not None)
        if not row:
            self.result_panel.set_title("결과")
            self.result_panel.set_image(None)
            self.result_info.setText("")
            return
        self.result_panel.set_title(f"결과 — {label_of(row['function'])}" + ("  ★" if row["favorite"] else ""))
        self.result_panel.set_image(self.ws.image_path(row))
        cost = row["est_cost_usd"]
        cost_text = "계산 불가(사용량 미제공)" if cost is None else f"${cost:.4f}"
        self.result_info.setText(
            f"{row['model']} · 품질 {row['quality']} · 크기 {row['size']} · 프리셋 {row['preset_name'] or '-'} · "
            f"{row['created_at'].replace('T', ' ')}\n앱 추정 실제 비용: {cost_text} (요금표 기준 추정, 청구액과 다를 수 있음) · "
            f"요청 ID: {row['request_id'] or '-'}")

    # =====================================================================
    # 이미지 입력
    # =====================================================================
    def open_files(self):
        if not self._ensure_project():
            return
        files, _ = QFileDialog.getOpenFileNames(self, "원본 이미지 열기", "", IMAGE_FILTER)
        if files:
            self.import_files(files)

    def import_files(self, files):
        if not self._ensure_project():
            return
        last, errors = None, []
        for f in files:
            if Path(f).suffix.lower() not in SUPPORTED_EXT:
                errors.append(f"{Path(f).name}: 지원하지 않는 형식 (PNG/JPG/WEBP만 가능)")
                continue
            try:
                last = self.ws.import_original(self.project_id, f)
            except (ValueError, OSError) as e:
                errors.append(str(e))
        self.reload_lists()
        if last:
            self.set_input(last)
        if errors:
            QMessageBox.warning(self, "가져오기", "\n".join(errors))

    def import_qimage(self, qimg):
        if qimg is None or qimg.isNull() or not self._ensure_project():
            return
        try:
            iid = self.ws.import_original(self.project_id, qimage_to_pil(qimg))
        except Exception as e:
            QMessageBox.warning(self, "붙여넣기", f"이미지를 가져오지 못했습니다: {e}")
            return
        self.reload_lists()
        self.set_input(iid)

    def paste(self):
        cb = QGuiApplication.clipboard()
        mime = cb.mimeData()
        local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
        if local:
            self.import_files(local)
        elif mime.hasImage() and not cb.image().isNull():
            self.import_qimage(cb.image())
        else:
            QMessageBox.information(self, "붙여넣기", "클립보드에 이미지가 없습니다.\n"
                                    "브라우저에서 이미지를 우클릭 → '이미지 복사'한 뒤 다시 시도하세요.")

    def _on_url_drop(self, url):
        QMessageBox.information(self, "가져오기", "웹 주소만 전달되어 이미지를 가져올 수 없습니다.\n"
                                "브라우저에서 이미지를 우클릭 → '이미지 복사' 후 Ctrl+V 로 붙여넣거나, 파일로 저장한 뒤 끌어다 놓으세요.")

    def _originals_menu(self, pos):
        item = self.originals.itemAt(pos)
        if not item:
            return
        iid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        a_copy = menu.addAction("복사")
        a_meta = menu.addAction("이름·태그 편집…")
        a_asset = menu.addAction("에셋 등록…")
        a_retouch = menu.addAction("부분 수정…")
        a_style = menu.addAction("화풍 추출…")
        a_del = menu.addAction("삭제")
        chosen = menu.exec(self.originals.mapToGlobal(pos))
        if chosen is a_meta:
            self._edit_meta_of(iid)
        elif chosen is a_asset:
            self._register_asset_of(iid)
        elif chosen is a_retouch:
            self.retouch(iid)
        elif chosen is a_copy:
            self.copy_image(iid)
        elif chosen is a_style:
            self.extract_style(iid)
        elif chosen is a_del:
            self._delete_image(iid)

    # =====================================================================
    # 생성 패널
    # =====================================================================
    def reload_models(self):
        self._loading = True
        current = self.model_combo.currentData() or self.ws.settings.get("last_model")
        self.model_combo.clear()
        for mid in self.ws.catalog.model_ids():
            self.model_combo.addItem(self.ws.catalog.label(mid), mid)
        idx = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._loading = False
        self._on_model_changed()

    def _fill(self, combo: QComboBox, values, wanted, editable=False):
        combo.blockSignals(True)
        combo.clear()
        combo.setEditable(editable)
        combo.addItems(values)
        if wanted in values:
            combo.setCurrentText(wanted)
        elif editable and wanted:
            combo.setEditText(wanted)
        combo.setEnabled(len(values) > 1 or editable)
        combo.blockSignals(False)

    def _on_model_changed(self, *_):
        if self._loading:
            return
        mid = self.model_combo.currentData()
        if not mid:
            self.estimate_label.setText("요금표에 모델이 없습니다. [설정 > 모델·요금표]를 확인하세요.")
            return
        m = self.ws.catalog.get(mid)
        s = self.ws.settings
        # 지원하지 않는 옵션은 목록에서 빠지고, 선택지가 하나뿐이면 비활성화된다.
        self._fill(self.quality_combo, m["qualities"], self.quality_combo.currentText() or s.get("last_quality"))
        self._fill(self.size_combo, m["sizes"], self.size_combo.currentText() or s.get("last_size"),
                   editable=bool(m.get("custom_size")))
        self._fill(self.bg_combo, m["backgrounds"], self.bg_combo.currentText() or s.get("last_background"))
        self.fidelity_combo.setEnabled(bool(m.get("input_fidelity")))
        if not m.get("input_fidelity"):
            self.fidelity_combo.setCurrentIndex(0)
        notes = []
        if m.get("deprecated"):
            notes.append("⚠ 지원 종료 예정/구형 모델입니다.")
        if m.get("custom_size"):
            notes.append("크기를 직접 입력할 수 있습니다 (16의 배수, 비율 1:3~3:1).")
        if "transparent" in m["backgrounds"]:
            notes.append("투명 배경은 모델에 따라 지원되지 않거나 불완전할 수 있습니다.")
        self.model_note.setText("\n".join(notes))
        self.model_note.setToolTip(m.get("note") or "")
        self.model_combo.setToolTip(m.get("note") or "")
        s.set("last_model", mid)
        self.update_estimate()

    def _on_option_changed(self, *_):
        if self._loading:
            return
        s = self.ws.settings
        s.set("last_quality", self.quality_combo.currentText(), save=False)
        s.set("last_size", self.size_combo.currentText(), save=False)
        s.set("last_background", self.bg_combo.currentText())
        self.update_estimate()

    def _select_function(self, key):
        self.function_key = key
        self.fn_buttons[key].setChecked(True)
        self.fn_desc.setText(BY_KEY[key].description)
        self.reload_presets()

    def reload_presets(self, select_id=None):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem(f"기본 (prompts/{self.function_key}.md)", None)
        for p in self.ws.db.list_presets(self.function_key):
            self.preset_combo.addItem(p["name"], p["id"])
        idx = self.preset_combo.findData(select_id) if select_id else 0
        self.preset_combo.setCurrentIndex(max(0, idx))
        self.preset_combo.blockSignals(False)
        self._on_preset_changed()

    def _on_preset_changed(self, *_):
        p = self.ws.db.get_preset(self.preset_combo.currentData())
        if p:
            if p["model"] and self.model_combo.findData(p["model"]) >= 0:
                self.model_combo.setCurrentIndex(self.model_combo.findData(p["model"]))
            for combo, val in ((self.quality_combo, p["quality"]), (self.size_combo, p["size"]),
                               (self.bg_combo, p["background"])):
                if val and (combo.findText(val) >= 0 or combo.isEditable()):
                    combo.setCurrentText(val)
        self.update_estimate()

    def save_as_preset(self):
        prompt, _ = self.ws.resolve_prompt(self.function_key, self.preset_combo.currentData())
        name, ok = QInputDialog.getText(self, "프리셋 저장", f"[{label_of(self.function_key)}] 프리셋 이름:\n"
                                        "(현재 프롬프트와 모델·품질·크기·배경 설정이 저장됩니다)")
        if not ok or not name.strip():
            return
        pid = self.ws.db.add_preset(name.strip(), self.function_key, prompt, self.model_combo.currentData(),
                                    self.quality_combo.currentText(), self.size_combo.currentText(),
                                    self.bg_combo.currentText())
        self.reload_presets(pid)

    def current_options(self) -> dict:
        return {"model": self.model_combo.currentData() or "", "quality": self.quality_combo.currentText() or "auto",
                "size": (self.size_combo.currentText() or "auto").strip().lower().replace(" ", ""),
                "background": self.bg_combo.currentText() or "auto",
                "input_fidelity": self.fidelity_combo.currentData() if self.fidelity_combo.isEnabled() else None}

    def update_estimate(self):
        if self._loading:
            return
        o = self.current_options()
        if not o["model"]:
            return
        if self.ws.demo_mode:
            self._estimate_detail = "데모 모드: API를 호출하지 않으며 비용이 발생하지 않습니다. 결과는 실제 AI 결과가 아닙니다."
            self.estimate_label.setText("데모 모드 — 비용 없음 (가짜 결과)")
            return
        prompt, _ = self.ws.resolve_prompt(self.function_key, self.preset_combo.currentData())
        prompt = self.apply_style(prompt)
        est = self.ws.catalog.estimate(o["model"], o["quality"], o["size"], prompt, 1 if self.input_id else 0, self.ws.db)
        self._estimate_detail = est.text()
        self.estimate_label.setText(est.headline() + ("" if est.computable else " (사용 기록 없음)")
                                    + "\n추정치 · 입력 토큰 등 추가 비용 가능")
        self.estimate_label.setToolTip(self._estimate_detail)

    def set_sync_status(self, text, busy=False, error=False, tooltip=""):
        self.sync_btn.setText(text if len(text) < 60 else text[:57] + "…")
        self.sync_btn.setToolTip(tooltip or text + "\n(클릭: 지금 동기화)")
        self.sync_btn.setStyleSheet("color: #b3261e;" if error else ("color: #0b57d0;" if busy else "color: #333;"))

    def refresh_credit(self):
        c = self.ws.credit_status()
        if c is None:
            self.credit_btn.setText("💳 충전액 미설정")
            self.credit_btn.setToolTip("충전한 금액을 입력하면 앱 기록 기준 추정 잔액을 보여 줍니다. (클릭 → 설정)")
            self.credit_btn.setStyleSheet("color: #666;")
            return
        low = c["remaining"] < max(0.5, c["total"] * 0.1)
        self.credit_btn.setText(f"💳 충전 ${c['total']:.2f} · 사용 ${c['used']:.2f} · 추정 잔액 ${c['remaining']:.2f}")
        self.credit_btn.setStyleSheet("color: #b3261e; font-weight: bold;" if low else "color: #333;")
        tip = (f"충전 기준일 {c['since'] or '(전체 기간)'} 이후 이 앱이 기록한 추정 사용액을 뺀 값입니다.\n"
               "다른 앱에서 쓴 금액, 과금 여부 불명 요청, 세금 등은 반영되지 않습니다. 실제 잔액은 OpenAI 결제 페이지에서 확인하세요.")
        if c["unknown"] or c["uncosted"]:
            tip += f"\n(합계에 포함되지 않은 요청: 과금 불명 {c['unknown']}건, 비용 계산 불가 {c['uncosted']}건)"
        self.credit_btn.setToolTip(tip)

    def refresh_api_status(self):
        if self.ws.demo_mode:
            self.api_status.setText("● 데모 모드 (API 호출 없음)")
            self.api_status.setStyleSheet("color: #b36b00; font-weight: bold;")
        elif keystore.get_key():
            self.api_status.setText("● API 키 등록됨")
            self.api_status.setStyleSheet("color: #1a7f37; font-weight: bold;")
        else:
            self.api_status.setText("○ API 키 없음 (생성 불가, 그 외 기능 사용 가능)")
            self.api_status.setStyleSheet("color: #b3261e; font-weight: bold;")
        self.refresh_credit()
        self.update_estimate()

    # =====================================================================
    # 생성
    # =====================================================================
    def generate(self):
        if not self._ensure_project():
            return
        row = self.ws.db.get_image(self.input_id)
        if not row:
            QMessageBox.information(self, "입력 이미지", "먼저 원본 이미지를 열거나 붙여넣어 입력을 지정하세요.")
            return
        o = self.current_options()
        prompt, preset_name = self.ws.resolve_prompt(self.function_key, self.preset_combo.currentData())
        prompt = self.apply_style(prompt)
        job = GenJob(project_id=self.project_id, function=self.function_key, prompt=prompt,
                     preset_id=self.preset_combo.currentData(), preset_name=preset_name,
                     input_paths=[self.ws.image_path(row)], source_image_id=row["id"], **o)
        self.ctl.start(job, self)

    def _on_started(self, job):
        self._started_at = time.monotonic()
        self.generate_btn.setEnabled(False)
        self.progress.show()
        self.cancel_btn.show()
        self._tick()
        self.timer.start()

    def _tick(self):
        sec = int(time.monotonic() - self._started_at)
        self.progress_label.setText(f"[{label_of(self.ctl.job.function) if self.ctl.job else ''}] 생성 중… {sec}초 경과 "
                                    f"(복잡한 요청은 2분 이상 걸릴 수 있습니다)")

    def _stop_progress(self):
        self.timer.stop()
        self.progress.hide()
        self.cancel_btn.hide()
        self.generate_btn.setEnabled(True)

    def _on_finished(self, image_id, job):
        self._stop_progress()
        if job.function == "retouch" and self._pending_retouch:
            self._retouch_states.pop(self._pending_retouch[0], None)
        if job.function.startswith("part_views_"):
            self._part_states.pop(job.extra_params.get("from_image_id"), None)
        self._pending_retouch = None
        if job.extra_params.get("batch") and self._batch:                  # v0.6.0 일괄 생성: 다음 단계
            if job.project_id == self.project_id:
                self.reload_lists()
                self.show_result(image_id)
            self._batch_next(image_id)
            return
        if job.extra_params.get("set_step") == 1 and self._pending_set:   # v0.6.0 세트: 헤어 이어서
            if job.project_id == self.project_id:
                self.reload_lists()
            self.progress_label.setText('세트 1/2 완료 — 헤어 사면도를 이어서 보냅니다…')
            self._continue_set_later(image_id, job)
            return
        self.progress_label.setText(f"완료 ({int(time.monotonic() - self._started_at)}초)")
        if job.project_id == self.project_id:
            self.result_id = image_id
            self.reload_lists()
            self.show_result(image_id)
        self.update_estimate()

    def _on_failed(self, message, job):
        self._stop_progress()
        if self._batch and job.extra_params.get("batch"):   # 일괄 생성은 실패하면 멈춘다 (자동 재시도 없음)
            b, self._batch = self._batch, None
            message += f"\n\n(일괄 생성: {b['done']}/{b['total']}장 완료 후 멈췄습니다. 남은 것은 보내지 않았습니다.)"
        if self._pending_set and job.extra_params.get("set_step") == 1:  # 헤드 실패 → 헤어는 보내지 않음
            self.ws.delete_image(self._pending_set["hair_part_id"])
            self._pending_set = None
            message += "\n\n(헤드+헤어 세트: 헤드가 실패해서 헤어는 보내지 않았습니다.)"
        self._pending_retouch = None  # 칠한 내용은 _retouch_states 에 남아 있어 다시 열면 복원된다
        self.progress_label.setText("실패 — 자동 재시도하지 않습니다.")
        QMessageBox.warning(self, "생성 실패", message)

    # =====================================================================
    # 결과 작업
    # =====================================================================
    def _result_row(self):
        return self.ws.db.get_image(self.result_id)

    def save_result_as(self):
        row = self._result_row()
        if not row:
            return
        src = self.ws.image_path(row)
        default = (row["name"] or src.stem) + ".png"
        dst, _ = QFileDialog.getSaveFileName(self, "PNG로 저장", default, "PNG (*.png)")
        if dst:
            if not dst.lower().endswith(".png"):
                dst += ".png"
            try:
                shutil.copyfile(src, dst)
            except OSError as e:
                QMessageBox.warning(self, "저장", f"저장하지 못했습니다: {e}")

    def toggle_favorite(self):
        row = self._result_row()
        if row:
            self.ws.db.update_image(row["id"], favorite=0 if row["favorite"] else 1)
            self.ws.write_project_json(row["project_id"])
            self.itemsEdited.emit()
            self.reload_lists()
            self.show_result(row["id"])

    def edit_meta(self):
        if self.result_id:
            self._edit_meta_of(self.result_id)

    def _edit_meta_of(self, image_id):
        row = self.ws.db.get_image(image_id)
        dlg = MetaDialog(row, self)
        if dlg.exec():
            self.ws.db.update_image(image_id, **dlg.values())
            self.ws.write_project_json(row["project_id"])
            self.itemsEdited.emit()
            self.reload_lists()
            if image_id == self.result_id:
                self.show_result(image_id)
            if image_id == self.input_id:
                self.set_input(image_id)

    def register_asset(self):
        if self.result_id:
            self._register_asset_of(self.result_id)

    def _register_asset_of(self, image_id):
        row = self.ws.db.get_image(image_id)
        fn = BY_KEY.get(row["function"] or "")
        dlg = AssetDialog(fn.default_category if fn else "Character", row["name"] or "", row["tags"] or "", self)
        if dlg.exec():
            v = dlg.values()
            try:
                self.ws.register_asset(self.ws.image_path(row), v["category"], v["name"], v["tags"], v["note"],
                                       source_image_id=image_id)
            except Exception as e:
                QMessageBox.warning(self, "에셋 등록", f"등록하지 못했습니다: {e}")
                return
            self.assetsChanged.emit()
            QMessageBox.information(self, "에셋 등록", "에셋 라이브러리에 등록했습니다.")

    def crop_asset(self):
        row = self._result_row()
        if not row:
            return
        fn = BY_KEY.get(row["function"] or "")
        dlg = CropDialog(self.ws.image_path(row), fn.default_category if fn else "Character", row["name"] or "", self)
        if dlg.exec():
            v = dlg.values()
            try:
                self.ws.register_asset(dlg.result_image(), v["category"], v["name"], v["tags"],
                                       source_image_id=row["id"])
            except Exception as e:
                QMessageBox.warning(self, "에셋 등록", f"등록하지 못했습니다: {e}")
                return
            self.assetsChanged.emit()
            QMessageBox.information(self, "에셋 등록", "선택 영역을 에셋으로 등록했습니다. 계속해서 다른 영역도 등록할 수 있습니다.")

    # ---- 화풍 -------------------------------------------------------------
    def reload_styles(self, select_id=None):
        self.style_combo.blockSignals(True)
        current = select_id if select_id is not None else self.style_combo.currentData()
        self.style_combo.clear()
        self.style_combo.addItem("적용 안 함", None)
        for st in self.ws.db.list_styles():
            self.style_combo.addItem(st["name"], st["id"])
        idx = self.style_combo.findData(current) if current else 0
        self.style_combo.setCurrentIndex(max(0, idx))
        self.style_combo.blockSignals(False)
        self._on_style_changed()

    def _on_style_changed(self, *_):
        sid = self.style_combo.currentData()
        self.ws.settings.set("last_style_id", sid)
        st = self.ws.db.get_style(sid)
        self.style_combo.setToolTip(st["prompt"] if st else "화풍 프롬프트를 붙이지 않습니다.")
        self.update_estimate()

    def apply_style(self, prompt: str) -> str:
        st = self.ws.db.get_style(self.style_combo.currentData())
        return compose_prompt(prompt, st["prompt"] if st else None)

    def extract_style_result(self):
        if self.result_id:
            self.extract_style(self.result_id)

    def extract_style(self, image_id):
        row = self.ws.db.get_image(image_id)
        if row:
            self.style_ctl.start(row, self)

    # ---- 복사 -------------------------------------------------------------
    def copy_image(self, image_id) -> bool:
        row = self.ws.db.get_image(image_id)
        ok = bool(row) and copy_image_to_clipboard(self.ws.image_path(row))
        if ok:
            self.progress_label.setText("클립보드에 복사했습니다.")
        return ok

    def copy_result(self):
        if self.result_id:
            self.copy_image(self.result_id)

    def copy_current(self):
        self.copy_image(self.result_id or self.input_id)

    def _history_menu(self, pos):
        item = self.history.itemAt(pos)
        if not item:
            return
        self.history.setCurrentItem(item)
        menu = QMenu(self)
        for text, fn in (("복사", self.copy_result), ("PNG로 저장…", self.save_result_as),
                         ("★ 즐겨찾기 표시/해제", self.toggle_favorite), ("이름·태그…", self.edit_meta), (None, None),
                         ("입력으로 사용", self.use_result_as_input), ("부분 수정…", self.retouch_result),
                         ("파트 사면도…", self.part_views_result),
                         ("헤드와 겹쳐 보기…", self.overlay_with_head), ("화풍 추출…", self.extract_style_result),
                         (None, None), ("에셋 등록…", self.register_asset), ("잘라서 에셋 등록…", self.crop_asset),
                         (None, None), ("삭제", self.delete_result)):
            if text is None:
                menu.addSeparator()
            else:
                menu.addAction(text).triggered.connect(fn)
        menu.exec(self.history.mapToGlobal(pos))

    # ---- 파트 사면도 ------------------------------------------------------
    def part_views_result(self):
        if self.result_id:
            self.part_views(self.result_id)

    def _head_sheets(self, image_id):
        """이 프로젝트의 헤드 사면도 결과 [(id, 이름)] 최신순 + 같은 원본에서 나온 최신 것(기본 선택)."""
        root = self.ws.root_original_id(image_id)
        rows = [r for r in self.ws.db.list_images(self.project_id, "output") if r["function"] == "part_views_head"]
        items = [(r["id"], f"#{r['id']} {r['name'] or '헤드 사면도'} ({(r['created_at'] or '')[:16]})") for r in rows]
        same = next((r["id"] for r in rows if self.ws.root_original_id(r["id"]) == root), None)
        return items, same

    def part_views(self, image_id):
        from .retouch_dialog import SET_KEY, PartViewsDialog
        if not self._ensure_project():
            return
        row = self.ws.db.get_image(image_id)
        if not row:
            return
        root_id = self.ws.root_original_id(image_id)
        original = self.ws.db.get_image(root_id) if root_id and root_id != image_id else None
        original_path = self.ws.image_path(original) if original else None
        has_original = bool(original_path and original_path.exists())
        sheets, default_head = self._head_sheets(image_id)
        dlg = PartViewsDialog(self.ws.image_path(row), has_original=has_original,
                              state=self._part_states.get(image_id), head_sheets=sheets, default_head=default_head,
                              parent=self)
        accepted = dlg.exec()
        self._part_states[image_id] = dlg.state()
        if not accepted:
            return
        use_ref = has_original and dlg.use_original.isChecked()
        key = dlg.part_type.currentData()
        try:
            if key == SET_KEY:    # v0.6.0: 헤드 → (그 결과를 기준으로) 헤어
                head_part, hair_part = dlg.isolated("head"), dlg.isolated("hair")
            else:
                part = dlg.isolated()
        except ValueError as e:
            QMessageBox.warning(self, "파트 사면도", str(e))
            return
        if key == SET_KEY:
            hair_id = self.ws.save_part_input(self.project_id, hair_part, image_id, f"헤어 (가발) 파트 (#{image_id})")
            self._pending_set = {"hair_part_id": hair_id, "from_image_id": image_id, "use_ref": use_ref,
                                 "original_path": original_path}
            if not self._start_part_job("part_views_head", head_part, image_id, use_ref, original_path,
                                        extra={"set_step": 1, "set_total": 2}):
                self.ws.delete_image(hair_id)
                self._pending_set = None
            return
        self._start_part_job(key, part, image_id, use_ref, original_path, head_sheet_id=dlg.head_sheet_id())

    def _start_part_job(self, key, part, from_image_id, use_ref, original_path, *, head_sheet_id=None,
                        part_id=None, extra=None, ask=True) -> bool:
        """잘라낸 파트(PIL 또는 이미 저장한 part_id) 하나로 파트 사면도 요청을 시작한다."""
        from .retouch_dialog import build_part_views_prompt
        label = dict((k, l) for l, k in PART_TYPES)[key]
        if part_id is None:
            part_id = self.ws.save_part_input(self.project_id, part, from_image_id, f"{label} 파트 (#{from_image_id})")
        head_path = None
        if head_sheet_id:
            hr = self.ws.db.get_image(head_sheet_id)
            head_path = self.ws.image_path(hr) if hr else None
            if not (head_path and head_path.exists()):
                head_path, head_sheet_id = None, None
        template, preset_name = self.ws.resolve_prompt(key, None)
        prompt = self.apply_style(build_part_views_prompt(template, use_ref, head_sheet=bool(head_path)))
        inputs = [self.ws.image_path(self.ws.db.get_image(part_id))] + ([head_path] if head_path else []) + \
            ([original_path] if use_ref else [])
        params = {"part_type": key, "from_image_id": from_image_id, "used_original_reference": use_ref}
        if head_sheet_id:
            params["head_sheet_id"] = head_sheet_id
        params.update(extra or {})
        job = GenJob(project_id=self.project_id, function=key, prompt=prompt, preset_id=None,
                     preset_name=preset_name, input_paths=inputs, source_image_id=part_id,
                     extra_params=params, **self.current_options())
        if not self.ctl.start(job, self, ask=ask):
            self.ws.delete_image(part_id)  # 요청하지 않았으면 잘라낸 입력을 남기지 않는다
            return False
        return True

    def _continue_set_later(self, head_id, job, tries=0):
        """완료 신호 직후에는 생성 워커가 아직 정리 전이라, 비워질 때까지 잠깐 기다렸다 보낸다."""
        from PySide6.QtCore import QTimer
        if self.ctl.is_busy() and tries < 200:
            QTimer.singleShot(25, lambda: self._continue_set_later(head_id, job, tries + 1))
            return
        if not self._continue_set(head_id, job):
            self.progress_label.setText('세트: 헤어 사면도를 보내지 않았습니다.')

    def _continue_set(self, head_id, job) -> bool:
        """세트 1/2(헤드)가 끝나면 그 결과를 기준으로 헤어를 이어서 보낸다."""
        pend, self._pending_set = self._pending_set, None
        if not pend or job.project_id != self.project_id:
            if pend:
                self.ws.delete_image(pend["hair_part_id"])
            return False
        ok = self._start_part_job("part_views_hair", None, pend["from_image_id"], pend["use_ref"],
                                  pend["original_path"], head_sheet_id=head_id, part_id=pend["hair_part_id"],
                                  extra={"set_step": 2, "set_total": 2}, ask=False)
        if ok:
            self.progress_label.setText("세트 2/2 — 헤어 사면도 생성 중 (헤드에 크기 맞춤)")
        return ok

    def overlay_with_head(self):
        """v0.6.0 정렬 가이드: 헤어 사면도 결과를 기준 헤드 사면도와 겹쳐 본다."""
        import json
        from .retouch_dialog import OverlayDialog
        row = self.ws.db.get_image(self.result_id) if self.result_id else None
        params = json.loads(row["params_json"]) if row and row["params_json"] else {}
        head = self.ws.db.get_image(params.get("head_sheet_id")) if params.get("head_sheet_id") else None
        if not head or not self.ws.image_path(head).exists():
            QMessageBox.information(self, "헤드와 겹쳐 보기", "이 결과는 헤드 사면도를 기준으로 만든 헤어 사면도가 아닙니다.\n"
                                    "[파트 사면도…]에서 '헤드+헤어 세트'로 만들거나, 헤어를 돌릴 때 기준 헤드를 고르세요.")
            return
        OverlayDialog(self.ws.image_path(head), self.ws.image_path(row), parent=self).exec()

    # ---- 핀터레스트 (v0.6.0) ---------------------------------------------
    def _estimate_steps(self, steps) -> float | None:
        o = self.current_options()
        total = 0.0
        for f in steps:
            try:
                est = self.ws.catalog.estimate(o["model"], o["quality"], o["size"], self.ws.default_prompt(f), 1, self.ws.db)
                v = est.for_budget()
            except Exception:
                v = None
            if v is None:
                return None
            total += v
        return total

    def open_pinterest(self):
        from .pinterest_ui import PinterestDialog
        dlg = PinterestDialog(self.ws, self.project_id, estimate_fn=self._estimate_steps, parent=self)
        if not dlg.exec() or not dlg.imported_ids:
            return
        self.reload_projects(dlg.project_id)
        self.set_input(dlg.imported_ids[0])
        if dlg.steps:
            self.run_batch(dlg.imported_ids, dlg.steps)
        else:
            QMessageBox.information(self, "핀터레스트", f"{len(dlg.imported_ids)}장을 원본으로 가져왔습니다 ('외부 출처' 태그).")

    def run_batch(self, image_ids, steps) -> bool:
        """여러 원본에 같은 단계를 순서대로 (앞 단계 결과 → 다음 입력). 시작 전에 장수·예상 비용을 한 번 확인."""
        if self.ctl.is_busy() or self._batch:
            QMessageBox.information(self, "생성 중", "이미 진행 중인 요청이 있습니다. 끝난 뒤 다시 시도하세요.")
            return False
        total = len(image_ids) * len(steps)
        if not self.ws.demo_mode:
            per = self._estimate_steps(steps)
            cost = f"예상 약 ${per * len(image_ids):.2f}" if per is not None else "예상 비용 계산 불가"
            names = " → ".join(label_of(f) for f in steps)
            if QMessageBox.question(self, "일괄 생성 확인 (유료)",
                                    f"{len(image_ids)}장 × [{names}] = 총 {total}장을 OpenAI 로 보냅니다.\n{cost}\n\n"
                                    "한 장씩 차례로 보내고, 실패하면 거기서 멈춥니다 (자동 재시도 없음).") \
                    != QMessageBox.StandardButton.Yes:
                return False
        self._batch = {"items": [(iid, list(steps)) for iid in image_ids], "done": 0, "total": total, "cur": None}
        self._batch_next(None)
        return True

    def _batch_next(self, last_id, tries=0):
        """완료 직후에는 워커 정리 전이라 비워질 때까지 기다렸다 다음 단계를 보낸다."""
        b = self._batch
        if not b:
            return
        if self.ctl.is_busy() and tries < 200:
            QTimer.singleShot(25, lambda: self._batch_next(last_id, tries + 1))
            return
        if last_id is not None:
            b["done"] += 1
            b["cur"] = last_id
        while b["items"] and not b["items"][0][1]:
            b["items"].pop(0)
            b["cur"] = None
        if not b["items"]:
            self._batch = None
            self.progress_label.setText(f"일괄 생성 완료 ({b['done']}/{b['total']}장)")
            return
        iid, steps = b["items"][0]
        f = steps.pop(0)
        src = self.ws.db.get_image(b["cur"] or iid)
        prompt = self.apply_style(self.ws.default_prompt(f))
        job = GenJob(project_id=src["project_id"], function=f, prompt=prompt, preset_id=None, preset_name="기본",
                     input_paths=[self.ws.image_path(src)], source_image_id=src["id"],
                     extra_params={"batch": f"{b['done'] + 1}/{b['total']}"}, **self.current_options())
        if not self.ctl.start(job, self, ask=False):
            self._batch = None
            return
        self.progress_label.setText(f"일괄 생성 {b['done'] + 1}/{b['total']} — [{label_of(f)}]")

    def open_billing_page(self):
        QDesktopServices.openUrl(QUrl("https://platform.openai.com/settings/organization/billing/overview"))

    # ---- 부분 수정 --------------------------------------------------------
    def retouch_result(self):
        if self.result_id:
            self.retouch(self.result_id)

    def retouch(self, image_id):
        from datetime import datetime

        from .retouch_dialog import RetouchDialog, build_retouch_prompt
        if not self._ensure_project():
            return
        row = self.ws.db.get_image(image_id)
        if not row:
            return
        path = self.ws.image_path(row)
        root_id = self.ws.root_original_id(image_id)
        original = self.ws.db.get_image(root_id) if root_id and root_id != image_id else None
        original_path = self.ws.image_path(original) if original else None
        has_original = bool(original_path and original_path.exists())
        o = self.current_options()
        m = self.ws.catalog.get(o["model"])
        dlg = RetouchDialog(path, has_original=has_original, model_label=m.get("label") or o["model"],
                            model_supports_markup=bool(m.get("markup")), state=self._retouch_states.get(image_id),
                            parent=self)
        accepted = dlg.exec()
        state = dlg.state()
        self._retouch_states[image_id] = state  # 닫거나 실패해도 칠한 내용을 다시 쓸 수 있게 보관
        if not accepted:
            return
        has_marks = dlg.canvas.has_marks()
        use_ref = has_original and dlg.use_original.isChecked()
        markup_path = None
        if has_marks:
            folder = self.ws.project_dir(self.project_id) / "markups"
            folder.mkdir(exist_ok=True)
            markup_path = folder / f"markup_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{image_id}.png"
            dlg.canvas.markup_image().save(markup_path, "PNG")
        template, preset_name = self.ws.resolve_prompt("retouch", None)
        prompt = self.apply_style(build_retouch_prompt(template, dlg.request_text(), has_marks, use_ref))
        inputs = [path] + ([original_path] if use_ref else [])
        job = GenJob(project_id=self.project_id, function="retouch", prompt=prompt, preset_id=None,
                     preset_name=preset_name, input_paths=inputs, source_image_id=image_id, markup_path=markup_path,
                     extra_params={"request": dlg.request_text(), "used_original_reference": use_ref,
                                   "markup_file": self.ws.paths.rel(markup_path) if markup_path else None}, **o)
        self._pending_retouch = (image_id, state)
        if not self.ctl.start(job, self):
            self._pending_retouch = None

    def use_result_as_input(self):
        if self.result_id:
            self.set_input(self.result_id)

    def delete_result(self):
        if self.result_id:
            self._delete_image(self.result_id)

    def _delete_image(self, image_id):
        row = self.ws.db.get_image(image_id)
        if not row:
            return
        refs = self.ws.db.image_references(image_id)
        text = f"'{row['name'] or '#' + str(image_id)}' 이미지를 삭제할까요? 파일도 함께 삭제됩니다."
        if refs["derived_outputs"] or refs["assets"]:
            text += (f"\n\n참조 확인: 이 이미지를 입력으로 만든 결과 {refs['derived_outputs']}개, "
                     f"이 이미지에서 등록한 에셋 {refs['assets']}개가 있습니다.\n"
                     "그 결과와 에셋 파일은 그대로 남고, '원본 참조' 연결만 끊어집니다.")
        if QMessageBox.question(self, "삭제 확인", text) != QMessageBox.StandardButton.Yes:
            return
        self.ws.delete_image(image_id)
        if image_id == self.input_id:
            self.set_input(None)
        if image_id == self.result_id:
            self.show_result(None)
        self.reload_lists()
        self.assetsChanged.emit()

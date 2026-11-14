"""에셋 라이브러리: 분류 / 검색 / 태그 / 썸네일 / 원본 참조."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..functions import CATEGORIES, CATEGORY_LABELS, label_of
from .dialogs import AssetDialog, category_combo, copy_image_to_clipboard, thumb_icon

IMAGE_FILTER = "이미지 (*.png *.jpg *.jpeg *.webp)"


class LibraryTab(QWidget):
    addToCompose = Signal(int)
    sendToProject = Signal(int)

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.asset_id = None
        lay = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("분류"))
        self.cats = QListWidget()
        self.cats.setFixedWidth(210)
        self.cats.currentRowChanged.connect(lambda *_: self.reload_assets())
        left.addWidget(self.cats, 1)
        b = QPushButton("파일에서 에셋 추가…")
        b.clicked.connect(self.add_from_files)
        left.addWidget(b)
        lay.addLayout(left)

        mid = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("이름 · 태그 · 메모 검색")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.reload_assets())
        mid.addWidget(self.search)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setIconSize(QSize(128, 128))
        self.grid.setGridSize(QSize(150, 168))
        self.grid.setWordWrap(True)
        self.grid.currentItemChanged.connect(self._on_select)
        self.grid.itemDoubleClicked.connect(lambda it: self.addToCompose.emit(it.data(Qt.ItemDataRole.UserRole)))
        mid.addWidget(self.grid, 1)
        self.count_label = QLabel()
        mid.addWidget(self.count_label)
        lay.addLayout(mid, 1)

        right = QVBoxLayout()
        self.preview = QLabel("에셋을 선택하세요")
        self.preview.setFixedSize(300, 300)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background: #ddd; border: 1px solid #bbb;")
        right.addWidget(self.preview)
        form = QFormLayout()
        self.name = QLineEdit()
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("쉼표로 구분")
        self.category = category_combo()
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)
        form.addRow("이름", self.name)
        form.addRow("분류", self.category)
        form.addRow("태그", self.tags)
        form.addRow("메모", self.note)
        right.addLayout(form)
        self.origin = QLabel()
        self.origin.setWordWrap(True)
        self.origin.setStyleSheet("color: #555;")
        right.addWidget(self.origin)
        self.buttons = []
        for text, fn in (("변경 저장", self.save_meta), ("조합 화면에 추가", lambda: self.addToCompose.emit(self.asset_id)),
                         ("현재 프로젝트의 입력으로 보내기", lambda: self.sendToProject.emit(self.asset_id)),
                         ("클립보드에 복사", self.copy), ("PNG로 내보내기…", self.export), ("삭제", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            right.addWidget(b)
            self.buttons.append(b)
        right.addStretch(1)
        box = QWidget()
        box.setLayout(right)
        box.setFixedWidth(320)
        lay.addWidget(box)
        self.reload()

    # ------------------------------------------------------------------
    def reload(self):
        row = max(0, self.cats.currentRow())
        self.cats.blockSignals(True)
        self.cats.clear()
        every = self.ws.db.list_assets()
        item = QListWidgetItem(f"전체 ({len(every)})")
        item.setData(Qt.ItemDataRole.UserRole, None)
        self.cats.addItem(item)
        for c in CATEGORIES:
            n = sum(1 for a in every if a["category"] == c)
            item = QListWidgetItem(f"{CATEGORY_LABELS[c]} ({n})")
            item.setData(Qt.ItemDataRole.UserRole, c)
            self.cats.addItem(item)
        self.cats.setCurrentRow(row)
        self.cats.blockSignals(False)
        self.reload_assets()

    def reload_assets(self):
        keep = self.asset_id
        item = self.cats.currentItem()
        cat = item.data(Qt.ItemDataRole.UserRole) if item else None
        rows = self.ws.db.list_assets(cat, self.search.text().strip())
        self.grid.blockSignals(True)
        self.grid.clear()
        selected = None
        for a in rows:
            it = QListWidgetItem(thumb_icon(self.ws.paths.abs(a["thumb_path"])), a["name"] or f"#{a['id']}")
            it.setData(Qt.ItemDataRole.UserRole, a["id"])
            it.setToolTip(f"{CATEGORY_LABELS.get(a['category'], a['category'])}\n태그: {a['tags'] or '-'}")
            self.grid.addItem(it)
            if a["id"] == keep:
                selected = it
        self.grid.blockSignals(False)
        self.count_label.setText(f"{len(rows)}개 · 더블클릭 = 조합 화면에 추가")
        if selected:
            self.grid.setCurrentItem(selected)
        else:
            self._show(None)

    def _on_select(self, item, _prev=None):
        self._show(item.data(Qt.ItemDataRole.UserRole) if item else None)

    def _show(self, asset_id):
        a = self.ws.db.get_asset(asset_id)
        self.asset_id = a["id"] if a else None
        for b in self.buttons:
            b.setEnabled(a is not None)
        if not a:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("에셋을 선택하세요")
            for w in (self.name, self.tags):
                w.clear()
            self.note.clear()
            self.origin.clear()
            return
        pm = QPixmap(str(self.ws.paths.abs(a["rel_path"])))
        if pm.isNull():
            self.preview.setText("파일을 찾을 수 없습니다")
        else:
            self.preview.setPixmap(pm.scaled(296, 296, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        self.name.setText(a["name"] or "")
        self.tags.setText(a["tags"] or "")
        self.note.setPlainText(a["note"] or "")
        self.category.setCurrentIndex(CATEGORIES.index(a["category"]))
        lines = [f"등록: {a['created_at'].replace('T', ' ')} · {pm.width()}×{pm.height()}px"]
        src = self.ws.db.get_image(a["source_image_id"])
        orig = self.ws.db.get_image(a["source_original_id"])
        if src:
            proj = self.ws.db.get_project(src["project_id"])
            what = "원본" if src["kind"] == "original" else f"생성 결과({label_of(src['function'])})"
            lines.append(f"출처: [{proj['name'] if proj else '?'}] {what} #{src['id']} {src['name'] or ''}")
        elif a["source_image_id"] is None and a["params_json"]:
            lines.append("출처 이미지는 삭제되었습니다 (에셋 사본은 유지됨).")
        if orig and (not src or orig["id"] != src["id"]):
            lines.append(f"최초 원화: #{orig['id']} {orig['name'] or ''}")
        if a["params_json"]:
            p = json.loads(a["params_json"])
            if p.get("model"):
                lines.append(f"생성 설정: {label_of(p.get('function'))} · {p.get('model')} · {p.get('quality')} · "
                             f"{p.get('size')} · 프리셋 {p.get('preset_name') or '-'}")
        self.origin.setText("\n".join(lines))

    # ------------------------------------------------------------------
    def add_from_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "에셋으로 추가할 이미지", "", IMAGE_FILTER)
        if not files:
            return
        item = self.cats.currentItem()
        dlg = AssetDialog((item.data(Qt.ItemDataRole.UserRole) if item else None) or "Character",
                          Path(files[0]).stem if len(files) == 1 else "", "", self)
        if not dlg.exec():
            return
        v = dlg.values()
        errors = []
        for f in files:
            try:
                self.ws.register_asset(f, v["category"], v["name"] if len(files) == 1 else Path(f).stem,
                                       v["tags"], v["note"])
            except Exception as e:
                errors.append(f"{Path(f).name}: {e}")
        self.reload()
        if errors:
            QMessageBox.warning(self, "에셋 추가", "\n".join(errors))

    def save_meta(self):
        if not self.asset_id:
            return
        self.ws.db.update_asset(self.asset_id, name=self.name.text().strip(), tags=self.tags.text().strip(),
                                note=self.note.toPlainText().strip(), category=self.category.currentData())
        self.reload()

    def copy(self):
        a = self.ws.db.get_asset(self.asset_id)
        if a:
            copy_image_to_clipboard(self.ws.paths.abs(a["rel_path"]))

    def export(self):
        a = self.ws.db.get_asset(self.asset_id)
        if not a:
            return
        dst, _ = QFileDialog.getSaveFileName(self, "PNG로 내보내기", (a["name"] or "asset") + ".png", "PNG (*.png)")
        if dst:
            try:
                shutil.copyfile(self.ws.paths.abs(a["rel_path"]), dst if dst.lower().endswith(".png") else dst + ".png")
            except OSError as e:
                QMessageBox.warning(self, "내보내기", f"저장하지 못했습니다: {e}")

    def delete(self):
        a = self.ws.db.get_asset(self.asset_id)
        if not a:
            return
        text = f"에셋 '{a['name']}'을(를) 라이브러리에서 삭제할까요? 라이브러리 내부 파일도 삭제됩니다."
        used = self.ws.db.compositions_using_asset(a["id"])
        if used:
            text += f"\n\n참조 확인: 저장된 조합 {len(used)}개({', '.join(used[:5])})에서 사용 중입니다. " \
                    "삭제하면 그 조합을 불러올 때 이 레이어가 빠집니다."
        if QMessageBox.question(self, "삭제 확인", text) == QMessageBox.StandardButton.Yes:
            self.ws.delete_asset(a["id"])
            self.asset_id = None
            self.reload()

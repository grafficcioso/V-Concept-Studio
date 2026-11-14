"""수동 레이어 조합 화면 (P1) + AI 조합 요청."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsView, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..functions import CATEGORY_LABELS
from .dialogs import copy_image_to_clipboard, qimage_to_pil, thumb_icon
from .gen_controller import GenJob
from .image_view import checker_brush

log = logging.getLogger(__name__)
MAX_AI_PARTS = 15  # 미리보기 1장 + 부품 15장 = API 한도 16장


class LayerItem(QGraphicsPixmapItem):
    def __init__(self, pixmap: QPixmap, asset_id, rel_path: str, name: str):
        super().__init__(pixmap)
        self.asset_id = asset_id
        self.rel_path = rel_path
        self.name = name
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setTransformOriginPoint(pixmap.width() / 2, pixmap.height() / 2)

    def to_dict(self) -> dict:
        return {"asset_id": self.asset_id, "rel_path": self.rel_path, "name": self.name,
                "x": round(self.pos().x(), 2), "y": round(self.pos().y(), 2), "scale": round(self.scale(), 4),
                "rotation": round(self.rotation(), 2), "visible": self.isVisible()}


class ComposeView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor(120, 120, 120))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, e):
        up = e.angleDelta().y() > 0
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:  # Ctrl+휠 = 선택 레이어 크기
            for it in self.scene().selectedItems():
                it.setScale(max(0.01, min(20, it.scale() * (1.05 if up else 1 / 1.05))))
            return
        f = 1.15 if up else 1 / 1.15
        if 0.03 < self.transform().m11() * f < 20:
            self.scale(f, f)


class AssetPicker(QDialog):
    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.setWindowTitle("라이브러리에서 레이어 추가 (여러 개 선택 가능)")
        self.resize(720, 480)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        from PySide6.QtCore import QSize
        self.list.setIconSize(QSize(110, 110))
        self.list.setGridSize(QSize(132, 150))
        for a in ws.db.list_assets():
            it = QListWidgetItem(thumb_icon(ws.paths.abs(a["thumb_path"])), a["name"] or f"#{a['id']}")
            it.setData(Qt.ItemDataRole.UserRole, a["id"])
            it.setToolTip(CATEGORY_LABELS.get(a["category"], a["category"]))
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        if not self.list.count():
            lay.addWidget(QLabel("라이브러리에 에셋이 없습니다. 생성 화면의 [에셋 등록] 또는 라이브러리의 [파일에서 에셋 추가]를 먼저 사용하세요."))
        lay.addWidget(self.list)
        lay.addWidget(buttons)

    def selected_ids(self):
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.list.selectedItems()]


class ComposeTab(QWidget):
    aiRequested = Signal()

    def __init__(self, ws, controller, get_project_id, get_options, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.ctl = controller
        self.get_project_id = get_project_id
        self.get_options = get_options
        self.layers: list[LayerItem] = []   # 아래 → 위 순서
        self.comp_id = None
        self._syncing = False
        self.style_provider = lambda prompt: prompt   # 메인 창이 생성 탭의 화풍 적용 함수로 바꿔 끼운다

        self.scene = QGraphicsScene(self)
        self.canvas = QGraphicsRectItem()
        self.canvas.setZValue(-1_000_000)
        self.canvas.setPen(QPen(QColor(40, 40, 40), 0))
        self.scene.addItem(self.canvas)
        self.view = ComposeView(self.scene)
        self.scene.selectionChanged.connect(self._on_scene_selection)
        self.scene.changed.connect(lambda *_: self._sync_spins())

        lay = QHBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addWidget(self._side_panel())
        self.set_canvas(1024, 1024, "white")

    # ------------------------------------------------------------------ UI
    def _side_panel(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(340)
        v = QVBoxLayout(side)

        box = QGroupBox("캔버스")
        f = QFormLayout(box)
        self.cw = QSpinBox()
        self.ch = QSpinBox()
        for s in (self.cw, self.ch):
            s.setRange(64, 8192)
            s.setValue(1024)
            s.setSuffix(" px")
            s.editingFinished.connect(self._canvas_changed)
        self.bg = QComboBox()
        self.bg.addItem("흰색", "white")
        self.bg.addItem("투명", "transparent")
        self.bg.currentIndexChanged.connect(self._canvas_changed)
        row = QHBoxLayout()
        row.addWidget(self.cw)
        row.addWidget(QLabel("×"))
        row.addWidget(self.ch)
        f.addRow("크기", row)
        f.addRow("배경", self.bg)
        b = QPushButton("화면에 맞춤")
        b.clicked.connect(self.fit_view)
        f.addRow(b)
        v.addWidget(box)

        box = QGroupBox("레이어 (위 = 앞)")
        l = QVBoxLayout(box)
        row = QHBoxLayout()
        for text, fn in (("라이브러리에서 추가…", self.add_from_library), ("파일에서 추가…", self.add_from_file)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        l.addLayout(row)
        self.layer_list = QListWidget()
        self.layer_list.currentRowChanged.connect(self._on_list_row)
        self.layer_list.itemChanged.connect(self._on_item_checked)
        l.addWidget(self.layer_list, 1)
        row = QHBoxLayout()
        for text, fn in (("앞으로", lambda: self._move(+1)), ("뒤로", lambda: self._move(-1)),
                         ("맨 앞", lambda: self._move(+999)), ("맨 뒤", lambda: self._move(-999)),
                         ("삭제", self.remove_selected)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        l.addLayout(row)
        v.addWidget(box, 1)

        box = QGroupBox("선택 레이어 변형 (드래그 이동 · Ctrl+휠 크기)")
        f = QFormLayout(box)
        self.sx, self.sy, self.ss, self.sr = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for s in (self.sx, self.sy):
            s.setRange(-20000, 20000)
            s.setDecimals(1)
        self.ss.setRange(1, 2000)
        self.ss.setSuffix(" %")
        self.ss.setDecimals(1)
        self.sr.setRange(-360, 360)
        self.sr.setSuffix(" °")
        self.sr.setDecimals(1)
        for s in (self.sx, self.sy, self.ss, self.sr):
            s.valueChanged.connect(self._apply_spins)
        f.addRow("X", self.sx)
        f.addRow("Y", self.sy)
        f.addRow("크기", self.ss)
        f.addRow("회전", self.sr)
        v.addWidget(box)

        box = QGroupBox("저장 · 내보내기")
        l = QVBoxLayout(box)
        row = QHBoxLayout()
        self.saved = QComboBox()
        row.addWidget(self.saved, 1)
        for text, fn in (("불러오기", self.load_selected), ("삭제", self.delete_saved)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        l.addLayout(row)
        row = QHBoxLayout()
        for text, fn in (("조합 저장", self.save), ("새 조합", self.new)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        l.addLayout(row)
        row = QHBoxLayout()
        b = QPushButton("합성 PNG 내보내기…")
        b.clicked.connect(self.export_png)
        row.addWidget(b)
        b = QPushButton("합성 이미지 복사")
        b.clicked.connect(self.copy_composite)
        row.addWidget(b)
        l.addLayout(row)
        b = QPushButton("AI 조합 (유료 생성 요청)…")
        b.setToolTip("조합 미리보기와 부품 이미지를 API에 보내 새 일러스트를 생성합니다.")
        b.clicked.connect(self.ai_compose)
        l.addWidget(b)
        note = QLabel("AI 조합은 별도의 유료 요청이며, 결과가 원본 부품과 완전히 일치한다고 보장되지 않습니다. "
                      "모델·품질·크기는 생성 화면의 현재 설정을 사용합니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        l.addWidget(note)
        v.addWidget(box)
        self.reload_saved()
        return side

    # -------------------------------------------------------------- canvas
    def set_canvas(self, w, h, bg):
        self._syncing = True
        self.cw.setValue(w)
        self.ch.setValue(h)
        self.bg.setCurrentIndex(max(0, self.bg.findData(bg)))
        self._syncing = False
        self.canvas.setRect(0, 0, w, h)
        self.canvas.setBrush(QBrush(Qt.GlobalColor.white) if bg == "white" else checker_brush(16))
        m = max(w, h)
        self.scene.setSceneRect(QRectF(-m, -m, w + 2 * m, h + 2 * m))
        self.fit_view()

    def _canvas_changed(self, *_):
        if not self._syncing:
            self.set_canvas(self.cw.value(), self.ch.value(), self.bg.currentData())

    def fit_view(self):
        self.view.resetTransform()
        self.view.fitInView(self.canvas.rect().adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, e):
        super().showEvent(e)
        self.fit_view()

    # -------------------------------------------------------------- layers
    def add_asset(self, asset_id) -> bool:
        a = self.ws.db.get_asset(asset_id)
        if not a:
            return False
        return self._add_layer(self.ws.paths.abs(a["rel_path"]), a["id"], a["rel_path"], a["name"] or f"#{a['id']}")

    def _add_layer(self, path: Path, asset_id, rel_path, name, state: dict | None = None) -> bool:
        pm = QPixmap(str(path))
        if pm.isNull():
            return False
        item = LayerItem(pm, asset_id, rel_path, name)
        self.scene.addItem(item)
        w, h = self.canvas.rect().width(), self.canvas.rect().height()
        if state:
            item.setPos(state.get("x", 0), state.get("y", 0))
            item.setScale(state.get("scale", 1))
            item.setRotation(state.get("rotation", 0))
            item.setVisible(state.get("visible", True))
        else:
            item.setScale(min(1.0, 0.8 * w / pm.width(), 0.8 * h / pm.height()))
            item.setPos((w - pm.width()) / 2, (h - pm.height()) / 2)
        self.layers.append(item)
        self._rebuild_list(select=item)
        return True

    def add_from_library(self):
        dlg = AssetPicker(self.ws, self)
        if dlg.exec():
            for aid in dlg.selected_ids():
                self.add_asset(aid)

    def add_from_file(self):
        files, _ = QFileDialog.getOpenFileNames(self, "레이어로 추가할 이미지", "", "이미지 (*.png *.jpg *.jpeg *.webp)")
        for f in files:
            # 파일 유실 방지를 위해 라이브러리에 사본으로 등록한 뒤 레이어로 쓴다.
            try:
                aid = self.ws.register_asset(f, "Character", Path(f).stem, "조합")
            except Exception as e:
                QMessageBox.warning(self, "레이어 추가", f"{Path(f).name}: {e}")
                continue
            self.add_asset(aid)

    def _rebuild_list(self, select: LayerItem | None = None):
        self._syncing = True
        self.layer_list.clear()
        for z, item in enumerate(self.layers):
            item.setZValue(z)
        for item in reversed(self.layers):
            it = QListWidgetItem(item.name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if item.isVisible() else Qt.CheckState.Unchecked)
            self.layer_list.addItem(it)
        self._syncing = False
        if select is not None and select in self.layers:
            self.scene.clearSelection()
            select.setSelected(True)

    def _row_of(self, item: LayerItem) -> int:
        return len(self.layers) - 1 - self.layers.index(item)

    def _item_of_row(self, row: int) -> LayerItem | None:
        return self.layers[len(self.layers) - 1 - row] if 0 <= row < len(self.layers) else None

    def selected(self) -> LayerItem | None:
        sel = [i for i in self.scene.selectedItems() if isinstance(i, LayerItem)]
        return sel[0] if sel else None

    def _on_scene_selection(self):
        if self._syncing:
            return
        item = self.selected()
        self._syncing = True
        self.layer_list.setCurrentRow(self._row_of(item) if item else -1)
        self._syncing = False
        self._sync_spins()

    def _on_list_row(self, row):
        if self._syncing:
            return
        item = self._item_of_row(row)
        self._syncing = True
        self.scene.clearSelection()
        if item is not None and item.isVisible():
            item.setSelected(True)
        self._syncing = False
        self._sync_spins(item)

    def _on_item_checked(self, list_item):
        if self._syncing:
            return
        item = self._item_of_row(self.layer_list.row(list_item))
        if item is not None:
            item.setVisible(list_item.checkState() == Qt.CheckState.Checked)

    def _current(self) -> LayerItem | None:
        return self.selected() or self._item_of_row(self.layer_list.currentRow())

    def _sync_spins(self, item: LayerItem | None = None):
        item = item or self._current()
        if item is None or self._syncing:
            return
        self._syncing = True
        self.sx.setValue(item.pos().x())
        self.sy.setValue(item.pos().y())
        self.ss.setValue(item.scale() * 100)
        self.sr.setValue(item.rotation())
        self._syncing = False

    def _apply_spins(self, *_):
        item = self._current()
        if item is None or self._syncing:
            return
        self._syncing = True
        item.setPos(self.sx.value(), self.sy.value())
        item.setScale(self.ss.value() / 100)
        item.setRotation(self.sr.value())
        self._syncing = False

    def _move(self, delta):
        item = self._current()
        if item is None:
            return
        i = self.layers.index(item)
        j = max(0, min(len(self.layers) - 1, i + delta))
        if i != j:
            self.layers.insert(j, self.layers.pop(i))
            self._rebuild_list(select=item)

    def remove_selected(self):
        item = self._current()
        if item is None:
            return
        self.layers.remove(item)
        self.scene.removeItem(item)
        self._rebuild_list()

    def clear_layers(self):
        for item in self.layers:
            self.scene.removeItem(item)
        self.layers = []
        self._rebuild_list()

    # ------------------------------------------------------- save / load
    def to_dict(self) -> dict:
        return {"version": 1, "canvas": {"width": self.cw.value(), "height": self.ch.value(),
                                         "background": self.bg.currentData()},
                "layers": [it.to_dict() for it in self.layers]}

    def load_dict(self, data: dict) -> list[str]:
        self.clear_layers()
        c = data.get("canvas", {})
        self.set_canvas(int(c.get("width", 1024)), int(c.get("height", 1024)), c.get("background", "white"))
        missing = []
        for l in data.get("layers", []):
            path = self.ws.paths.abs(l.get("rel_path"))
            if not (path and path.exists() and self._add_layer(path, l.get("asset_id"), l["rel_path"],
                                                               l.get("name", "layer"), l)):
                missing.append(l.get("name", "?"))
        self.scene.clearSelection()
        return missing

    def reload_saved(self, select_id=None):
        self.saved.clear()
        for c in self.ws.db.list_compositions():
            self.saved.addItem(f"{c['name']}  ({c['updated_at'][:16].replace('T', ' ')})", c["id"])
        if select_id:
            self.saved.setCurrentIndex(max(0, self.saved.findData(select_id)))

    def save(self):
        current = self.ws.db.get_composition(self.comp_id) if self.comp_id else None
        name, ok = QInputDialog.getText(self, "조합 저장", "조합 이름:", text=current["name"] if current else "")
        if not ok or not name.strip():
            return
        if current and name.strip() != current["name"]:
            self.comp_id = None  # 다른 이름 = 새 조합으로 저장
        data = self.to_dict()
        pid = self.get_project_id()
        self.comp_id = self.ws.db.save_composition(self.comp_id, pid, name.strip(), data)
        if pid:  # 프로젝트 폴더에도 다시 편집 가능한 JSON 사본을 둔다
            try:
                from ..workspace import safe_name
                folder = self.ws.project_dir(pid) / "compositions"
                folder.mkdir(exist_ok=True)
                (folder / f"{safe_name(name)}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                                                encoding="utf-8")
            except OSError as e:
                log.warning("조합 JSON 저장 실패: %s", e)
        self.reload_saved(self.comp_id)

    def load_selected(self):
        cid = self.saved.currentData()
        row = self.ws.db.get_composition(cid) if cid else None
        if not row:
            return
        if self.layers and QMessageBox.question(self, "불러오기", "현재 캔버스의 레이어를 지우고 불러올까요?") \
                != QMessageBox.StandardButton.Yes:
            return
        missing = self.load_dict(json.loads(row["json"]))
        self.comp_id = cid
        if missing:
            QMessageBox.warning(self, "불러오기", "다음 레이어의 에셋 파일을 찾을 수 없어 제외했습니다:\n" + "\n".join(missing))

    def delete_saved(self):
        cid = self.saved.currentData()
        if cid and QMessageBox.question(self, "삭제", "저장된 조합을 삭제할까요?") == QMessageBox.StandardButton.Yes:
            self.ws.db.delete_composition(cid)
            if self.comp_id == cid:
                self.comp_id = None
            self.reload_saved()

    def new(self):
        if self.layers and QMessageBox.question(self, "새 조합", "현재 캔버스를 비울까요?") != QMessageBox.StandardButton.Yes:
            return
        self.clear_layers()
        self.comp_id = None

    # -------------------------------------------------------------- export
    def render_image(self, background: str | None = None) -> QImage:
        """캔버스 영역을 QImage 로 렌더링. 투명 배경이면 알파가 유지된다."""
        background = background or self.bg.currentData()
        w, h = self.cw.value(), self.ch.value()
        selected = self.scene.selectedItems()
        self.scene.clearSelection()
        old_brush, old_pen = self.canvas.brush(), self.canvas.pen()
        self.canvas.setPen(QPen(Qt.PenStyle.NoPen))
        self.canvas.setBrush(QBrush(Qt.GlobalColor.white) if background == "white" else QBrush(Qt.BrushStyle.NoBrush))
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
        p.end()
        self.canvas.setBrush(old_brush)
        self.canvas.setPen(old_pen)
        for it in selected:
            it.setSelected(True)
        return img

    def export_png(self):
        if not self.layers:
            QMessageBox.information(self, "내보내기", "레이어가 없습니다.")
            return
        dst, _ = QFileDialog.getSaveFileName(self, "합성 PNG 내보내기", "composition.png", "PNG (*.png)")
        if dst:
            if not dst.lower().endswith(".png"):
                dst += ".png"
            if not self.render_image().save(dst, "PNG"):
                QMessageBox.warning(self, "내보내기", "저장하지 못했습니다.")

    def copy_composite(self):
        if not self.layers:
            QMessageBox.information(self, "복사", "레이어가 없습니다.")
            return
        copy_image_to_clipboard(self.render_image())

    # ------------------------------------------------------------ AI 조합
    def ai_compose(self):
        visible = [l for l in self.layers if l.isVisible()]
        if not visible:
            QMessageBox.information(self, "AI 조합", "보이는 레이어가 없습니다. 먼저 부품을 캔버스에 올려 주세요.")
            return
        pid = self.get_project_id()
        if not pid:
            QMessageBox.information(self, "AI 조합", "결과를 저장할 프로젝트가 필요합니다. 생성 화면에서 프로젝트를 만들어 주세요.")
            return
        parts, seen = [], set()
        for l in reversed(visible):  # 앞쪽 레이어부터
            p = self.ws.paths.abs(l.rel_path)
            if p and p.exists() and p not in seen:
                seen.add(p)
                parts.append(p)
        if len(parts) > MAX_AI_PARTS:
            QMessageBox.information(self, "AI 조합", f"부품이 {len(parts)}개입니다. 앞쪽 {MAX_AI_PARTS}개만 참조로 보냅니다.")
            parts = parts[:MAX_AI_PARTS]
        preview_id = self.ws.import_original(pid, qimage_to_pil(self.render_image("white")), name="조합 미리보기")
        preview = self.ws.image_path(self.ws.db.get_image(preview_id))
        prompt, preset_name = self.ws.resolve_prompt("ai_compose", None)
        prompt = self.style_provider(prompt)
        job = GenJob(project_id=pid, function="ai_compose", prompt=prompt, preset_id=None, preset_name=preset_name,
                     input_paths=[preview, *parts], source_image_id=preview_id,
                     extra_params={"composition": self.to_dict()}, **self.get_options())
        if self.ctl.start(job, self):
            self.aiRequested.emit()
        else:
            self.ws.delete_image(preview_id)  # 요청하지 않았으면 미리보기 사본을 남기지 않는다

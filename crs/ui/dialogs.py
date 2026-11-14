"""작은 대화상자와 Qt 유틸."""
from __future__ import annotations

import io

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QRubberBand, QSpinBox, QVBoxLayout)

from ..functions import CATEGORIES, CATEGORY_LABELS


def qimage_to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).copy()


def copy_image_to_clipboard(source) -> bool:
    """이미지를 클립보드에 복사한다. source: 파일 경로 또는 QImage.

    일반 이미지 데이터와 함께 PNG 데이터도 넣어, PNG를 받는 프로그램(포토샵 등)에서는 투명도가 유지되게 한다.
    """
    img = source if isinstance(source, QImage) else QImage(str(source))
    if img.isNull():
        return False
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    png = QByteArray(buf.data())
    mime = QMimeData()
    mime.setImageData(img)
    mime.setData("image/png", png)
    mime.setData("PNG", png)
    QGuiApplication.clipboard().setMimeData(mime)
    return True


def thumb_icon(path) -> QIcon:
    if path and path.exists():
        return QIcon(QPixmap(str(path)))
    pm = QPixmap(96, 96)
    pm.fill(Qt.GlobalColor.lightGray)
    return QIcon(pm)


def make_thumb_list(icon=84, height=118) -> QListWidget:
    lw = QListWidget()
    lw.setViewMode(QListWidget.ViewMode.IconMode)
    lw.setFlow(QListWidget.Flow.LeftToRight)
    lw.setWrapping(False)
    lw.setMovement(QListWidget.Movement.Static)
    lw.setIconSize(QSize(icon, icon))
    lw.setGridSize(QSize(icon + 24, icon + 34))
    lw.setFixedHeight(height + 24)
    lw.setWordWrap(False)
    lw.setTextElideMode(Qt.TextElideMode.ElideRight)
    return lw


def add_thumb_item(lw: QListWidget, text: str, thumb_path, row_id: int, tooltip="") -> QListWidgetItem:
    item = QListWidgetItem(thumb_icon(thumb_path), text)
    item.setData(Qt.ItemDataRole.UserRole, row_id)
    item.setToolTip(tooltip or text)
    lw.addItem(item)
    return item


def category_combo(current: str | None = None) -> QComboBox:
    cb = QComboBox()
    for c in CATEGORIES:
        cb.addItem(CATEGORY_LABELS[c], c)
    if current in CATEGORIES:
        cb.setCurrentIndex(CATEGORIES.index(current))
    return cb


class MetaDialog(QDialog):
    """이름/태그/메모/즐겨찾기 편집."""

    def __init__(self, row: dict, parent=None, with_favorite=True):
        super().__init__(parent)
        self.setWindowTitle("이름 · 태그 편집")
        self.name = QLineEdit(row.get("name") or "")
        self.tags = QLineEdit(row.get("tags") or "")
        self.tags.setPlaceholderText("쉼표로 구분 (예: 금발, 트윈테일)")
        self.note = QPlainTextEdit(row.get("note") or "")
        self.note.setFixedHeight(80)
        self.favorite = QCheckBox("즐겨찾기")
        self.favorite.setChecked(bool(row.get("favorite")))
        form = QFormLayout()
        form.addRow("이름", self.name)
        form.addRow("태그", self.tags)
        form.addRow("메모", self.note)
        if with_favorite:
            form.addRow("", self.favorite)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)
        self.resize(420, 240)

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "tags": self.tags.text().strip(),
                "note": self.note.toPlainText().strip(), "favorite": int(self.favorite.isChecked())}


class AssetDialog(QDialog):
    """에셋 등록 정보."""

    def __init__(self, default_category="Character", default_name="", default_tags="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("에셋 라이브러리에 등록")
        self.category = category_combo(default_category)
        self.name = QLineEdit(default_name)
        self.tags = QLineEdit(default_tags)
        self.tags.setPlaceholderText("쉼표로 구분")
        self.note = QLineEdit()
        form = QFormLayout()
        form.addRow("분류", self.category)
        form.addRow("이름", self.name)
        form.addRow("태그", self.tags)
        form.addRow("메모", self.note)
        info = QLabel("라이브러리 내부에 PNG 사본이 저장되므로, 프로젝트의 원본을 지워도 에셋은 유지됩니다.")
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(info)
        lay.addWidget(buttons)
        self.resize(420, 220)

    def values(self) -> dict:
        return {"category": self.category.currentData(), "name": self.name.text().strip(),
                "tags": self.tags.text().strip(), "note": self.note.text().strip()}


class CropLabel(QLabel):
    """이미지 위에서 드래그로 사각 영역을 고르는 라벨."""

    def __init__(self, pixmap: QPixmap, max_side=760):
        super().__init__()
        self.full = pixmap
        self.shown = pixmap.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation) \
            if max(pixmap.width(), pixmap.height()) > max_side else pixmap
        self.ratio = pixmap.width() / max(1, self.shown.width())
        self.setPixmap(self.shown)
        self.setFixedSize(self.shown.size())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        self.selection = QRect()

    def mousePressEvent(self, e):
        self.origin = e.position().toPoint()
        self.band.setGeometry(QRect(self.origin, QSize()))
        self.band.show()

    def mouseMoveEvent(self, e):
        self.band.setGeometry(QRect(self.origin, e.position().toPoint()).normalized() & self.rect())

    def mouseReleaseEvent(self, e):
        self.selection = self.band.geometry()

    def set_selection(self, rect: QRect):
        self.selection = rect & self.rect()
        self.band.setGeometry(self.selection)
        self.band.show()

    def image_box(self) -> tuple[int, int, int, int] | None:
        r = self.selection
        if r.width() < 4 or r.height() < 4:
            return None
        k = self.ratio
        return (int(r.left() * k), int(r.top() * k),
                min(self.full.width(), int((r.right() + 1) * k)), min(self.full.height(), int((r.bottom() + 1) * k)))


class CropDialog(QDialog):
    """시트 이미지에서 영역을 잘라 개별 에셋으로 등록한다 (수동 선택)."""

    def __init__(self, image_path, default_category="Hair", default_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("영역을 잘라 에셋으로 등록")
        self.image_path = image_path
        self.crop = CropLabel(QPixmap(str(image_path)))
        self.category = category_combo(default_category)
        self.name = QLineEdit(default_name)
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("쉼표로 구분")
        self.remove_bg = QCheckBox("단순 배경 제거 (가장자리와 이어진 단색 배경을 투명 처리)")
        self.tolerance = QSpinBox()
        self.tolerance.setRange(0, 120)
        self.tolerance.setValue(24)
        form = QFormLayout()
        form.addRow("분류", self.category)
        form.addRow("이름", self.name)
        form.addRow("태그", self.tags)
        form.addRow("", self.remove_bg)
        form.addRow("배경 허용 오차", self.tolerance)
        info = QLabel("이미지 위를 드래그해 부품 하나를 감싸는 영역을 선택하세요. 시트는 자동으로 개별 부품으로 인식되지 않습니다.\n"
                      "단순 배경 제거는 AI 처리가 아니며, 배경과 비슷한 색의 가장자리가 지워지거나 배경이 남을 수 있습니다.")
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("에셋 등록")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.crop, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def _accept(self):
        if self.crop.image_box() is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "영역 선택", "먼저 이미지 위를 드래그해 영역을 선택하세요.")
            return
        self.accept()

    def result_image(self) -> Image.Image:
        from ..workspace import remove_background_simple
        with Image.open(self.image_path) as im:
            part = im.convert("RGBA").crop(self.crop.image_box())
        if self.remove_bg.isChecked():
            part = remove_background_simple(part, self.tolerance.value())
        return part

    def values(self) -> dict:
        return {"category": self.category.currentData(), "name": self.name.text().strip(),
                "tags": self.tags.text().strip()}

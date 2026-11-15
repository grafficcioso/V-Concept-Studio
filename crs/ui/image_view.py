"""확대/축소 가능한 이미지 뷰어 + 드롭 존."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
                               QToolButton, QVBoxLayout, QWidget)


def checker_brush(size=12) -> QBrush:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(QColor(235, 235, 235))
    p = QPainter(pm)
    p.fillRect(0, 0, size, size, QColor(205, 205, 205))
    p.fillRect(size, size, size, size, QColor(205, 205, 205))
    p.end()
    return QBrush(pm)


class ImageView(QGraphicsView):
    filesDropped = Signal(list)
    imageDropped = Signal(QImage)
    urlDropped = Signal(str)

    def __init__(self, placeholder="", accept_drops=False, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None
        self._placeholder = placeholder
        self._auto_fit = True
        self._fitting = False
        self._last_fit_size = None
        self.setBackgroundBrush(checker_brush())
        # 스크롤바를 쓰지 않는다(드래그로 이동). 맞춤 → 스크롤바 생김/사라짐 → 크기 변경 → 다시 맞춤의
        # 반복을 원천 차단한다 (Windows 11 스타일 PC에서 화면 멈춤의 원인이었음).
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMinimumSize(220, 220)
        self.setAcceptDrops(accept_drops)

    # ---- image ----------------------------------------------------------
    def has_image(self) -> bool:
        return self._item is not None

    def set_image(self, path: str | Path | None):
        self._scene.clear()
        self._item = None
        if path and Path(path).exists():
            pm = QPixmap(str(path))
            if not pm.isNull():
                self._item = self._scene.addPixmap(pm)
                self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
                # 기본값(MaskShape)은 투명 이미지의 불투명 픽셀 외곽선을 통째로 경로로 만든다.
                # 머리카락처럼 복잡한 투명 결과에서는 이 계산이 몇 초씩 걸려 화면이 멈췄다 → 사각형으로.
                self._item.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
                self._scene.setSceneRect(QRectF(pm.rect()))
        if self._item is None:
            self._scene.setSceneRect(QRectF(0, 0, 1, 1))
        self._auto_fit = True
        self.fit()
        self.viewport().update()

    def fit(self):
        self._auto_fit = True
        if self._item is None or self._fitting:
            return
        self._fitting = True
        try:
            self.resetTransform()
            # 아이템(외곽선 계산) 대신 이미지 사각형 기준으로 맞춘다
            self.fitInView(self._item.sceneBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._last_fit_size = self.viewport().size()
        finally:
            self._fitting = False

    def actual_size(self):
        self._auto_fit = False
        self.resetTransform()

    def zoom(self, factor: float):
        if self._item is None:
            return
        self._auto_fit = False
        current = self.transform().m11()
        if 0.02 < current * factor < 40:
            self.scale(factor, factor)

    # ---- events ---------------------------------------------------------
    def wheelEvent(self, e):
        self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto_fit and not self._fitting and self.viewport().size() != self._last_fit_size:
            self.fit()

    def drawForeground(self, painter, rect):
        if self._item is None and self._placeholder:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor(110, 110, 110))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             self._placeholder)
            painter.restore()

    def _acceptable(self, mime) -> bool:
        return mime.hasUrls() or mime.hasImage()

    def dragEnterEvent(self, e):
        if self.acceptDrops() and self._acceptable(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self.acceptDrops() and self._acceptable(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        mime = e.mimeData()
        local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if local:
            self.filesDropped.emit(local)
        elif mime.hasImage():
            img = mime.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.imageDropped.emit(img)
        elif mime.hasUrls():
            self.urlDropped.emit(mime.urls()[0].toString())
        e.acceptProposedAction()


class ImagePanel(QWidget):
    """제목 + 확대/축소 버튼 + ImageView."""

    def __init__(self, title: str, placeholder="", accept_drops=False, parent=None):
        super().__init__(parent)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: bold;")
        self.view = ImageView(placeholder, accept_drops)
        bar = QHBoxLayout()
        bar.addWidget(self.title, 1)
        for text, tip, fn in (("−", "축소", lambda: self.view.zoom(1 / 1.25)), ("+", "확대", lambda: self.view.zoom(1.25)),
                              ("맞춤", "화면에 맞춤", self.view.fit), ("100%", "실제 크기", self.view.actual_size)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

    def set_title(self, text: str):
        self.title.setText(text)

    def set_image(self, path):
        self.view.set_image(path)

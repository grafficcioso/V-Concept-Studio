"""부분 수정: 빨간 반투명 펜으로 영역을 칠하고 수정 요청을 적는 대화상자.

칠한 영역은 OpenAI Images API 의 markup(주석 레이어, 투명 PNG)으로 전송된다.
모델은 이를 '안내'로 사용하므로 경계가 정확히 지켜지지 않을 수 있다.
"""
from __future__ import annotations

from PIL import Image, ImageOps
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from .dialogs import qimage_to_pil
from .image_view import checker_brush

RED = QColor(255, 0, 0, 255)
DISPLAY_OPACITY = 0.45   # 화면 표시용 반투명
EXPORT_OPACITY = 0.6     # API로 보내는 주석 레이어의 불투명도

QUICK_REQUESTS = [
    ("머리카락 가닥 줄이기",
     "Reduce the thin, scattered hair strands in this area; merge them into fewer, larger, clean hair clumps."),
    ("원화 느낌 더 살리기",
     "Make this area look closer to the original illustration's art style, linework, coloring, and shading."),
    ("원화 색감 맞추기", "Match the colors and saturation of this area to the original illustration."),
    ("선 깔끔하게", "Clean up the linework in this area: crisp, consistent line weight, no sketchy or broken lines."),
    ("가려진 부분 복원", "Reconstruct the hidden or cut-off part in this area naturally, consistent with the design."),
    ("형태를 원화처럼", "Reshape this area to match the shape and silhouette in the original illustration."),
]


def pil_to_qimage(im: Image.Image) -> QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    return QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888).copy()


class PaintCanvas(QWidget):
    """이미지 위에 빨간 펜 레이어를 칠하는 위젯. 좌표는 원본 이미지 픽셀 기준."""

    def __init__(self, base: QImage, overlay: QImage | None = None, parent=None):
        super().__init__(parent)
        self.base = base
        self.overlay = overlay.copy() if overlay is not None and overlay.size() == base.size() else \
            self._blank()
        self.brush = max(8, min(base.width(), base.height()) // 25)
        self.eraser = False
        self.ghost: QImage | None = None   # v0.6.0: 세트 모드의 다른 파트 (파란색, 칠해지지 않음)
        self.undo_stack: list[QImage] = []
        self._last: QPointF | None = None
        self._hover: QPointF | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(420, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _blank(self) -> QImage:
        img = QImage(self.base.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        return img

    # ---- geometry -------------------------------------------------------
    def _target(self) -> QRectF:
        w, h = self.base.width(), self.base.height()
        k = min(self.width() / w, self.height() / h)
        return QRectF((self.width() - w * k) / 2, (self.height() - h * k) / 2, w * k, h * k)

    def _scale(self) -> float:
        return self._target().width() / self.base.width()

    def _to_image(self, p: QPointF) -> QPointF:
        t = self._target()
        k = self._scale()
        return QPointF((p.x() - t.left()) / k, (p.y() - t.top()) / k)

    # ---- painting -------------------------------------------------------
    def _stroke(self, a: QPointF, b: QPointF):
        p = QPainter(self.overlay)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.eraser:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.setPen(QPen(RED, self.brush, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if a == b:
            p.setBrush(RED)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(a, self.brush / 2, self.brush / 2)
        else:
            p.drawLine(a, b)
        p.end()
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.undo_stack.append(self.overlay.copy())
        del self.undo_stack[:-30]
        pt = self._to_image(e.position())
        self._last = pt
        self._stroke(pt, pt)

    def mouseMoveEvent(self, e):
        self._hover = e.position()
        if self._last is not None and e.buttons() & Qt.MouseButton.LeftButton:
            pt = self._to_image(e.position())
            self._stroke(self._last, pt)
            self._last = pt
        else:
            self.update()

    def mouseReleaseEvent(self, e):
        self._last = None

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def undo(self):
        if self.undo_stack:
            self.overlay = self.undo_stack.pop()
            self.update()

    def clear(self):
        if self.has_marks():
            self.undo_stack.append(self.overlay.copy())
        self.overlay = self._blank()
        self.update()

    def has_marks(self) -> bool:
        return qimage_to_pil(self.overlay).getchannel("A").getbbox() is not None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self._target()
        p.fillRect(t, checker_brush())
        p.drawImage(t, self.base)
        p.setOpacity(DISPLAY_OPACITY)
        if self.ghost is not None:
            p.drawImage(t, self.ghost)
        p.drawImage(t, self.overlay)
        p.setOpacity(1.0)
        if self._hover is not None:
            r = self.brush * self._scale() / 2
            p.setPen(QPen(QColor(0, 0, 0), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self._hover, r, r)
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
            p.drawEllipse(self._hover, r, r)
        p.end()

    # ---- export ---------------------------------------------------------
    def markup_image(self) -> Image.Image:
        """API로 보낼 주석 레이어: 원본과 같은 크기의 투명 PNG, 칠한 곳만 반투명 빨강."""
        out = QImage(self.base.size(), QImage.Format.Format_ARGB32)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setOpacity(EXPORT_OPACITY)
        p.drawImage(0, 0, self.overlay)
        p.end()
        return qimage_to_pil(out).convert("RGBA")


class RetouchDialog(QDialog):
    def __init__(self, image_path, *, has_original: bool, model_label: str, model_supports_markup: bool,
                 state: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("부분 수정 — 빨간 펜으로 영역을 칠하고 요청을 적으세요")
        self.resize(1280, 820)
        with Image.open(image_path) as im:
            base = ImageOps.exif_transpose(im).convert("RGBA")
        state = state or {}
        self.canvas = PaintCanvas(pil_to_qimage(base), state.get("overlay"))

        side = QVBoxLayout()
        box = QGroupBox("펜")
        g = QGridLayout(box)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        top = max(16, min(base.width, base.height) // 4)
        self.size_slider.setRange(2, top)
        self.size_slider.setValue(min(self.canvas.brush, top))
        self.size_slider.valueChanged.connect(self._on_size)
        self.size_label = QLabel()
        self._on_size(self.size_slider.value())
        g.addWidget(QLabel("굵기"), 0, 0)
        g.addWidget(self.size_slider, 0, 1)
        g.addWidget(self.size_label, 0, 2)
        self.eraser = QCheckBox("지우개")
        self.eraser.toggled.connect(lambda v: setattr(self.canvas, "eraser", v))
        g.addWidget(self.eraser, 1, 0)
        b = QPushButton("되돌리기 (Ctrl+Z)")
        b.setShortcut("Ctrl+Z")
        b.clicked.connect(self.canvas.undo)
        g.addWidget(b, 1, 1)
        b = QPushButton("모두 지우기")
        b.clicked.connect(self.canvas.clear)
        g.addWidget(b, 1, 2)
        side.addWidget(box)

        box = QGroupBox("수정 요청")
        v = QVBoxLayout(box)
        self.request = QPlainTextEdit(state.get("request", ""))
        self.request.setPlaceholderText("예: 머리카락 가닥을 줄이고 큰 덩어리로 정리해줘 / 원화 느낌을 더 살려줘\n"
                                        "(한국어로 써도 됩니다. 아래 버튼은 영문 문장을 추가합니다.)")
        v.addWidget(self.request, 1)
        grid = QGridLayout()
        for i, (label, text) in enumerate(QUICK_REQUESTS):
            qb = QPushButton(label)
            qb.setToolTip(text)
            qb.clicked.connect(lambda _=False, t=text: self._append(t))
            grid.addWidget(qb, i // 2, i % 2)
        v.addLayout(grid)
        side.addWidget(box, 1)

        self.use_original = QCheckBox("원본 원화를 참조 이미지로 함께 보내기 (원화 느낌 살리기에 필요)")
        self.use_original.setChecked(state.get("use_original", True) and has_original)
        self.use_original.setEnabled(has_original)
        if not has_original:
            self.use_original.setToolTip("이 이미지가 원본이거나, 연결된 원본을 찾을 수 없습니다.")
        side.addWidget(self.use_original)

        notes = [f"모델: {model_label}"]
        if not model_supports_markup:
            notes.append("⚠ 이 모델은 빨간 펜 영역 지정을 지원하지 않습니다. 칠하지 않고 전체 수정으로 보내거나, "
                         "생성 화면에서 GPT-Image-2.5/2.6 계열 모델로 바꾸세요.")
        notes += ["· 칠한 영역은 '안내'로 쓰여 경계가 정확히 지켜지지 않을 수 있고, 바깥도 조금 바뀔 수 있습니다.",
                  "· 아무 곳도 칠하지 않으면 이미지 전체에 대한 수정 요청이 됩니다.",
                  "· 유료 생성 요청 1회입니다. 원본 원화를 함께 보내면 입력 이미지 비용이 조금 늘어납니다."]
        info = QLabel("\n".join(notes))
        info.setWordWrap(True)
        info.setStyleSheet("color: #555;")
        side.addWidget(info)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("수정 요청 보내기 (유료)")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("닫기 (칠한 내용 유지)")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        side.addWidget(buttons)

        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(380)
        lay = QHBoxLayout(self)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(panel)

    def _on_size(self, value):
        self.canvas.brush = value
        self.size_label.setText(f"{value}px")
        self.canvas.update()

    def _append(self, text):
        cur = self.request.toPlainText().rstrip()
        self.request.setPlainText((cur + "\n" if cur else "") + text)

    def _accept(self):
        if not self.request_text():
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "수정 요청", "어떻게 바꿀지 요청 내용을 적어 주세요.")
            return
        self.accept()

    def request_text(self) -> str:
        return self.request.toPlainText().strip()

    def state(self) -> dict:
        return {"overlay": self.canvas.overlay.copy(), "request": self.request.toPlainText(),
                "use_original": self.use_original.isChecked()}


AREA_WITH_MARKUP = ("A separate annotation layer marks the area to change with semi-transparent red paint. "
                    "Apply the requested change only inside the red-marked area and blend it naturally with the "
                    "surrounding image.")
AREA_WHOLE = "Apply the requested change to the whole image."
REFERENCE_ORIGINAL = ("The SECOND image is the original source illustration of the same character. Use it as the "
                      "reference for the character's original look, art style, linework, colors, and hair shapes.")


ORDINALS = {2: "SECOND", 3: "THIRD"}
PART_REFERENCE = ("The {n} image is the original full character illustration, given only as a reference for scale, "
                  "proportions, and art style. Do not draw any other part from it.")
# v0.6.0: 헤어 사면도를 먼저 만든 민머리 헤드 사면도에 맞춘다 (크기·눈선·턱선)
HEAD_SHEET_REFERENCE = (
    "The SECOND image is the four-view sheet of the bald head that this wig must fit. Use exactly the same view order, "
    "panel layout, panel positions, and head scale as that sheet, so each wig view can be laid directly over the "
    "matching head view: match the head width, skull height and shape, hairline, ear position, eye line, and jaw/chin "
    "line. Do not draw the face, skin, or head itself; draw only the wig where it would sit on that head.")
SET_KEY = "set_head_hair"   # 파트 종류 콤보의 '헤드+헤어 세트' 값
GHOST_COLOR = QColor(40, 110, 255)


def build_retouch_prompt(template: str, request: str, has_markup: bool, has_reference: bool) -> str:
    from ..functions import fill_placeholders, join_prompt, split_prompt
    pos, neg = split_prompt(template or "{area_instruction} {reference_instruction}\n\nRequested change: {request}")
    if "{request}" not in pos:
        pos += "\n\nRequested change: {request}"
    text = join_prompt(pos, neg)
    return fill_placeholders(text, area_instruction=AREA_WITH_MARKUP if has_markup else AREA_WHOLE,
                             reference_instruction=REFERENCE_ORIGINAL if has_reference else "",
                             request=request.strip())


def build_part_views_prompt(template: str, has_reference: bool, head_sheet: bool = False) -> str:
    """입력 순서: 1) 잘라낸 파트 2) (헤드 사면도) 3) (원본 원화). 안내 문장의 '몇 번째 이미지'를 거기에 맞춘다."""
    from ..functions import fill_placeholders, join_prompt, split_prompt
    parts = []
    if head_sheet:
        parts.append(HEAD_SHEET_REFERENCE)
    if has_reference:
        parts.append(PART_REFERENCE.replace("{n}", ORDINALS[3 if head_sheet else 2]))
    ref = " ".join(parts)
    if ref and "{reference_instruction}" not in (template or ""):
        pos, neg = split_prompt(template)   # 사용자가 자리표시자를 지운 프롬프트에도 안내는 붙인다
        template = join_prompt(pos + "\n\n{reference_instruction}", neg)
    return fill_placeholders(template, reference_instruction=ref)


def tint_overlay(overlay: QImage, color: QColor = GHOST_COLOR) -> QImage:
    """칠한 레이어를 다른 색으로 바꾼 사본 (세트 모드에서 지금 안 칠하는 파트를 보여 주는 용도)."""
    out = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawImage(0, 0, overlay)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return out


class PartViewsDialog(QDialog):
    """파트 사면도: 돌릴 파트를 빨간 펜으로 칠하고 파트 종류를 고른다.

    v0.6.0: '헤드+헤어 세트' = 헤드(빨강)·헤어(파랑)를 따로 칠해 두 장을 이어서 생성.
            헤어 하나만 돌릴 때도 이미 만든 헤드 사면도를 크기 기준으로 함께 보낼 수 있다.
    head_sheets: [(image_id, 표시 이름)] 최신순, default_head: 기본으로 고를 헤드 사면도 id
    """

    def __init__(self, image_path, *, has_original: bool, state: dict | None = None, head_sheets=None,
                 default_head: int | None = None, parent=None):
        from PySide6.QtWidgets import QButtonGroup, QComboBox, QRadioButton
        from ..functions import PART_TYPES
        super().__init__(parent)
        self.setWindowTitle("파트 사면도 — 돌릴 파트를 칠하세요")
        self.resize(1280, 860)
        with Image.open(image_path) as im:
            self.base_pil = ImageOps.exif_transpose(im).convert("RGBA")
        state = state or {}
        self.canvas = PaintCanvas(pil_to_qimage(self.base_pil), state.get("overlay"))
        layers = state.get("layers") or {}
        self.layers = {k: (layers[k].copy() if layers.get(k) is not None and layers[k].size() == self.canvas.base.size()
                           else self.canvas._blank()) for k in ("head", "hair")}
        self.cur_layer = None
        side = QVBoxLayout()
        box = QGroupBox("펜")
        g = QGridLayout(box)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        top = max(16, min(self.base_pil.size) // 4)
        self.size_slider.setRange(2, top)
        self.size_slider.setValue(min(self.canvas.brush * 2, top))
        self.size_label = QLabel()
        self.size_slider.valueChanged.connect(self._on_size)
        self._on_size(self.size_slider.value())
        g.addWidget(QLabel("굵기"), 0, 0)
        g.addWidget(self.size_slider, 0, 1)
        g.addWidget(self.size_label, 0, 2)
        self.eraser = QCheckBox("지우개")
        self.eraser.toggled.connect(lambda v: setattr(self.canvas, "eraser", v))
        g.addWidget(self.eraser, 1, 0)
        b = QPushButton("되돌리기 (Ctrl+Z)")
        b.setShortcut("Ctrl+Z")
        b.clicked.connect(self.canvas.undo)
        g.addWidget(b, 1, 1)
        b = QPushButton("모두 지우기")
        b.clicked.connect(self.canvas.clear)
        g.addWidget(b, 1, 2)
        side.addWidget(box)
        box = QGroupBox("파트 종류")
        v = QVBoxLayout(box)
        self.part_type = QComboBox()
        for label, key in PART_TYPES:
            self.part_type.addItem(label, key)
        self.part_type.addItem("헤드+헤어 세트 (2장, 크기 맞춤)", SET_KEY)
        idx = self.part_type.findData(state.get("part_type"))
        self.part_type.setCurrentIndex(max(0, idx))
        v.addWidget(self.part_type)
        # 세트 모드: 지금 칠하는 파트
        self.layer_box = QWidget()
        h = QHBoxLayout(self.layer_box)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel("칠하는 파트:"))
        self.layer_head = QRadioButton("헤드")
        self.layer_hair = QRadioButton("헤어")
        self._layer_group = QButtonGroup(self)
        for rb in (self.layer_head, self.layer_hair):
            self._layer_group.addButton(rb)
            h.addWidget(rb)
        self.layer_head.setChecked(True)
        self.layer_head.toggled.connect(lambda on: self._switch_layer("head" if on else "hair"))
        v.addWidget(self.layer_box)
        # 헤어 단독: 크기 기준 헤드 사면도
        self.head_box = QWidget()
        hv = QVBoxLayout(self.head_box)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(QLabel("크기 기준 헤드 사면도 (이 헤드에 맞춰 헤어를 그림):"))
        self.head_sheet = QComboBox()
        self.head_sheet.addItem("(쓰지 않음)", None)
        for iid, label in head_sheets or []:
            self.head_sheet.addItem(label, iid)
        want = state.get("head_sheet", default_head)
        i = self.head_sheet.findData(want) if want else -1
        self.head_sheet.setCurrentIndex(max(0, i))
        hv.addWidget(self.head_sheet)
        v.addWidget(self.head_box)
        side.addWidget(box)
        self.use_original = QCheckBox("원본 원화를 참조로 함께 보내기 (크기·형태·화풍 맞추기용)")
        self.use_original.setChecked(state.get("use_original", True) and has_original)
        self.use_original.setEnabled(has_original)
        side.addWidget(self.use_original)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #555;")
        side.addWidget(self.info)
        self.preview = QLabel("칠한 뒤 [잘라낸 모습 미리보기]를 누르면 보낼 이미지를 확인할 수 있습니다.")
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(200)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background: #eee; border: 1px solid #ccc;")
        b = QPushButton("잘라낸 모습 미리보기")
        b.clicked.connect(self.show_preview)
        side.addWidget(b)
        side.addWidget(self.preview, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("닫기 (칠한 내용 유지)")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        side.addWidget(buttons)
        panel = QWidget()
        panel.setLayout(side)
        panel.setFixedWidth(400)
        lay = QHBoxLayout(self)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(panel)
        self.part_type.currentIndexChanged.connect(self._on_type)
        if state.get("part_type") == SET_KEY:   # 세트 상태 복원
            self.cur_layer = "head"
            self._apply_layer()
            if state.get("layer") == "hair":
                self.layer_hair.setChecked(True)
        self._on_type()

    # ---- 모드 ------------------------------------------------------------
    def is_set(self) -> bool:
        return self.part_type.currentData() == SET_KEY

    def _on_type(self, *_):
        is_set = self.is_set()
        if is_set and self.cur_layer is None:            # 일반 → 세트: 지금 칠한 것은 헤드 레이어로
            self.layers["head"] = self.canvas.overlay
            self.cur_layer = "head"
            self.layer_head.blockSignals(True)
            self.layer_head.setChecked(True)
            self.layer_head.blockSignals(False)
            self._apply_layer()
        elif not is_set and self.cur_layer is not None:  # 세트 → 일반: 지금 칠하던 레이어를 그대로 씀
            self.layers[self.cur_layer] = self.canvas.overlay
            self.cur_layer = None
            self.canvas.ghost = None
            self.canvas.update()
        self.layer_box.setVisible(is_set)
        self.head_box.setVisible(self.part_type.currentData() == "part_views_hair" and self.head_sheet.count() > 1)
        self.ok_btn.setText("세트 생성 (유료 2장)" if is_set else "사면도 생성 (유료)")
        if is_set:
            lines = ["· [헤드]를 고르고 민머리를 칠한 뒤, [헤어]를 고르고 가발을 칠하세요. 지금 칠하는 파트는 빨강, 다른 파트는 파랑으로 보입니다.",
                     "· 헤드 사면도를 먼저 만들고, 그 결과를 크기 기준으로 넣어 헤어 사면도를 이어서 만듭니다 "
                     "(머리 크기·눈선·턱선 맞춤).",
                     "· 유료 생성 2회입니다. 헤드가 실패하면 헤어는 보내지 않습니다.",
                     "· AI가 크기를 완벽히 맞춘다는 보장은 없습니다. 결과에서 [헤드와 겹쳐 보기]로 확인하세요."]
        else:
            lines = ["· 돌릴 파트 하나를 넉넉하게 감싸도록 칠하세요. 칠한 부분만 잘라내고 나머지는 흰색으로 지운 뒤 보냅니다.",
                     "· 다른 파트가 함께 칠해지면 같이 돌아갈 수 있습니다.",
                     "· 4개 뷰의 일관성은 완벽하게 보장되지 않습니다.",
                     "· 유료 생성 요청 1회입니다. 모델·품질·크기는 생성 화면 설정을 따릅니다."]
        self.info.setText("\n".join(lines))

    def _switch_layer(self, name):
        if self.cur_layer is None or name == self.cur_layer:
            return
        self.layers[self.cur_layer] = self.canvas.overlay
        self.cur_layer = name
        self.canvas.undo_stack.clear()
        self._apply_layer()

    def _apply_layer(self):
        other = "hair" if self.cur_layer == "head" else "head"
        self.canvas.overlay = self.layers[self.cur_layer]
        self.canvas.ghost = tint_overlay(self.layers[other])
        self.canvas.update()

    def _layer_image(self, name) -> QImage:
        return self.canvas.overlay if self.cur_layer == name else self.layers[name]

    def _on_size(self, value):
        self.canvas.brush = value
        self.size_label.setText(f"{value}px")
        self.canvas.update()

    # ---- 결과 ------------------------------------------------------------
    def isolated(self, layer: str | None = None) -> Image.Image:
        from ..workspace import isolate_part
        img = self._layer_image(layer) if layer else self.canvas.overlay
        return isolate_part(self.base_pil, qimage_to_pil(img).convert("RGBA"))

    def head_sheet_id(self) -> int | None:
        if self.part_type.currentData() != "part_views_hair":
            return None
        return self.head_sheet.currentData()

    def show_preview(self):
        from PySide6.QtGui import QPixmap
        try:
            img = self.isolated(self.cur_layer)
        except ValueError as e:
            self.preview.setText(str(e))
            return
        pm = QPixmap.fromImage(pil_to_qimage(img))
        self.preview.setPixmap(pm.scaled(360, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    @staticmethod
    def _has(img: QImage) -> bool:
        return qimage_to_pil(img).getchannel("A").getbbox() is not None

    def _accept(self):
        from PySide6.QtWidgets import QMessageBox
        if self.is_set():
            missing = [n for k, n in (("head", "헤드"), ("hair", "헤어")) if not self._has(self._layer_image(k))]
            if missing:
                QMessageBox.information(self, "파트 사면도", f"{'·'.join(missing)} 파트를 칠해 주세요. "
                                        "(칠하는 파트를 바꿔 가며 둘 다 칠해야 합니다)")
                return
        elif not self.canvas.has_marks():
            QMessageBox.information(self, "파트 사면도", "돌릴 파트를 빨간 펜으로 칠해 주세요.")
            return
        self.accept()

    def state(self) -> dict:
        return {"overlay": self.canvas.overlay.copy(), "part_type": self.part_type.currentData(),
                "use_original": self.use_original.isChecked(),
                "layers": {k: self._layer_image(k).copy() for k in ("head", "hair")}, "layer": self.cur_layer,
                "head_sheet": self.head_sheet.currentData()}


class OverlayDialog(QDialog):
    """v0.6.0 정렬 가이드: 헤어 사면도를 헤드 사면도 위에 반투명으로 겹쳐 본다 (폭을 맞춤)."""

    def __init__(self, below_path, above_path, title="헤드와 겹쳐 보기", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1100, 760)
        with Image.open(below_path) as a, Image.open(above_path) as b:
            self.below = a.convert("RGBA")
            above = b.convert("RGBA")
        if above.size != self.below.size:
            k = self.below.width / above.width
            above = above.resize((self.below.width, max(1, round(above.height * k))), Image.LANCZOS)
            canvas = Image.new("RGBA", self.below.size, (255, 255, 255, 255))
            canvas.paste(above, (0, 0))
            above = canvas
        self.above = above
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumSize(600, 400)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.valueChanged.connect(self.render_view)
        self.label = QLabel()
        row = QHBoxLayout()
        row.addWidget(QLabel("헤드"))
        row.addWidget(self.slider, 1)
        row.addWidget(QLabel("헤어"))
        row.addWidget(self.label)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        tip = QLabel("슬라이더로 비율을 바꿔 머리 크기·눈선·턱선이 맞는지 보세요. 두 장의 크기가 다르면 폭을 맞춰 겹칩니다.")
        tip.setStyleSheet("color: #555;")
        lay.addWidget(tip)
        self.render_view()

    def composite(self) -> Image.Image:
        return Image.blend(self.below, self.above, self.slider.value() / 100.0)

    def render_view(self, *_):
        from PySide6.QtGui import QPixmap
        self.label.setText(f"{self.slider.value()}%")
        pm = QPixmap.fromImage(pil_to_qimage(self.composite()))
        w, h = max(600, self.view.width()), max(400, self.view.height())
        self.view.setPixmap(pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))

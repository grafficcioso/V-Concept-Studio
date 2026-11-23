"""v0.6.0 핀터레스트 가져오기 창: 등록한 공개 보드의 핀 목록 → 골라서 원본으로 가져오기 → (선택) 정면화·사면도.

네트워크는 작업 스레드에서만 한다(창이 멈추지 않게). 생성 자체는 생성 탭의 일괄 생성(run_batch)이 맡는다.
"""
from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QDate, QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QHBoxLayout, QLabel, QListView, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

from .. import pinterest as pt

log = logging.getLogger(__name__)

STEP_CHOICES = [("가져오기만 (생성 없음 · 무료)", []), ("정면화", ["front_view"]), ("사면도", ["four_views"]),
                ("정면화 → 사면도", ["front_view", "four_views"])]


class BoardLoader(QThread):
    loaded = Signal(object)    # [(Pin, thumb_bytes|None)]
    failed = Signal(str)

    def __init__(self, board, get=None, parent=None):
        super().__init__(parent)
        self.board, self.get = board, get or pt.http_get

    def run(self):
        try:
            pins = pt.fetch_board(self.board, get=self.get)
        except pt.PinterestError as e:
            self.failed.emit(str(e))
            return
        except Exception as e:
            log.exception("pinterest load")
            self.failed.emit(f"목록을 불러오지 못했습니다: {e}")
            return
        out = []
        for p in pins:
            try:
                out.append((p, self.get(p.thumb_url, 5 * 1024 * 1024)))
            except Exception:
                out.append((p, None))
        self.loaded.emit(out)


class PinDownloader(QThread):
    progress = Signal(int, int)
    done = Signal(object, object)   # [(Pin, bytes, url)], [에러 문장]

    def __init__(self, pins, get=None, parent=None):
        super().__init__(parent)
        self.pins, self.get = pins, get or pt.http_get

    def run(self):
        ok, errors = [], []
        for i, p in enumerate(self.pins):
            self.progress.emit(i, len(self.pins))
            try:
                data, url = pt.download_image(p, get=self.get)
                ok.append((p, data, url))
            except Exception as e:
                errors.append(str(e))
        self.progress.emit(len(self.pins), len(self.pins))
        self.done.emit(ok, errors)


class PinterestDialog(QDialog):
    """accept 후: imported_ids, steps, project_id 를 읽는다."""

    def __init__(self, ws, project_id, estimate_fn=None, get=None, parent=None):
        super().__init__(parent)
        self.ws, self.get, self.estimate_fn = ws, get, estimate_fn
        self.imported_ids: list[int] = []
        self.steps: list[str] = []
        self.project_id = project_id
        self.pins: list = []
        self._threads = []
        self.setWindowTitle("핀터레스트 가져오기 (공개 보드)")
        self.resize(980, 720)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("보드"))
        self.board = QComboBox()
        self.board.setEditable(True)
        self.board.setMinimumWidth(460)
        self.board.lineEdit().setPlaceholderText("https://www.pinterest.com/사용자이름/보드이름/")
        for b in ws.settings.get("pinterest_boards") or []:
            self.board.addItem(b)
        last = ws.settings.get("pinterest_last_board")
        if last:
            self.board.setCurrentText(last)
        row.addWidget(self.board, 1)
        for text, fn in (("보드 저장", self.save_board), ("목록에서 빼기", self.remove_board), ("핀 불러오기", self.load)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
            if text == "핀 불러오기":
                self.load_btn = b
        lay.addLayout(row)
        self.status = QLabel("공개 보드만 됩니다. 최근 핀 약 25개를 보여 줍니다 (핀터레스트 RSS, 무료).")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(150, 150))
        self.list.setGridSize(QSize(176, 206))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setWordWrap(True)
        self.list.itemChanged.connect(self.update_summary)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.since_on = QCheckBox("이 날짜 이후 핀만")
        self.since = QDateEdit(QDate.currentDate().addDays(-7))
        self.since.setCalendarPopup(True)
        self.since.setDisplayFormat("yyyy-MM-dd")
        row.addWidget(self.since_on)
        row.addWidget(self.since)
        b = QPushButton("조건에 맞는 것 모두 선택")
        b.clicked.connect(self.select_matching)
        row.addWidget(b)
        b = QPushButton("선택 해제")
        b.clicked.connect(lambda: self._check_all(False))
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("가져온 뒤"))
        self.step = QComboBox()
        for label, steps in STEP_CHOICES:
            self.step.addItem(label, steps)
        self.step.currentIndexChanged.connect(self.update_summary)
        row.addWidget(self.step)
        row.addWidget(QLabel("프로젝트"))
        self.project = QComboBox()
        self.project.setEditable(True)
        for p in ws.db.list_projects():
            self.project.addItem(p["name"], p["id"])
        cur = ws.db.get_project(project_id) if project_id else None
        self.project.setCurrentText(cur["name"] if cur else "핀터레스트")
        row.addWidget(self.project, 1)
        lay.addLayout(row)
        tip = QLabel("가져온 이미지에는 '외부 출처' 태그와 출처 메모가 붙습니다 — 공부·참고용, 판매·커미션·게시 금지.\n"
                     "날짜는 RSS 의 핀 날짜입니다 (보드에 추가한 날짜와 다를 수 있음). 이미 가져온 핀은 다시 가져오지 않습니다.")
        tip.setStyleSheet("color: #555;")
        lay.addWidget(tip)
        row = QHBoxLayout()
        self.summary = QLabel()
        row.addWidget(self.summary, 1)
        self.go_btn = QPushButton("가져오기")
        self.go_btn.clicked.connect(self.do_import)
        row.addWidget(self.go_btn)
        b = QPushButton("닫기")
        b.clicked.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)
        self.update_summary()

    # ---- 보드 목록 --------------------------------------------------------
    def save_board(self):
        text = self.board.currentText().strip()
        try:
            pt.board_rss_url(text)
        except pt.PinterestError as e:
            QMessageBox.warning(self, "핀터레스트", str(e))
            return
        boards = list(self.ws.settings.get("pinterest_boards") or [])
        if text not in boards:
            boards.append(text)
            self.ws.settings.set("pinterest_boards", boards)
            self.board.addItem(text)
        self.status.setText("보드를 저장했습니다.")

    def remove_board(self):
        text = self.board.currentText().strip()
        boards = [b for b in (self.ws.settings.get("pinterest_boards") or []) if b != text]
        self.ws.settings.set("pinterest_boards", boards)
        i = self.board.findText(text)
        if i >= 0:
            self.board.removeItem(i)

    # ---- 불러오기 ---------------------------------------------------------
    def load(self):
        text = self.board.currentText().strip()
        try:
            pt.board_rss_url(text)
        except pt.PinterestError as e:
            QMessageBox.warning(self, "핀터레스트", str(e))
            return
        self.ws.settings.set("pinterest_last_board", text)
        self.load_btn.setEnabled(False)
        self.status.setText("불러오는 중…")
        t = BoardLoader(text, self.get, self)
        t.loaded.connect(self._on_loaded)
        t.failed.connect(self._on_failed)
        t.finished.connect(lambda: self.load_btn.setEnabled(True))
        self._threads.append(t)
        t.start()

    def _on_failed(self, msg):
        self.status.setText("✖ " + msg)

    def _on_loaded(self, items):
        seen = self.ws.db.pinterest_seen()
        self.list.blockSignals(True)
        self.list.clear()
        self.pins = []
        for pin, thumb in items:
            it = QListWidgetItem(f"{pin.date_text}\n{pin.title[:40]}")
            it.setToolTip(f"{pin.title}\n{pin.link}")
            if thumb:
                pm = QPixmap()
                if pm.loadFromData(thumb):
                    it.setIcon(QIcon(pm))
            it.setData(Qt.ItemDataRole.UserRole, len(self.pins))
            if pin.pin_id in seen:
                it.setText(f"✔ 가져옴 · {pin.date_text}\n{pin.title[:34]}")
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            else:
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked)
            self.pins.append(pin)
            self.list.addItem(it)
        self.list.blockSignals(False)
        new = sum(1 for p in self.pins if p.pin_id not in seen)
        self.status.setText(f"핀 {len(self.pins)}개 (새 핀 {new}개). 가져올 핀을 체크하세요.")
        self.update_summary()

    def _check_all(self, on, pred=lambda pin: True):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                pin = self.pins[it.data(Qt.ItemDataRole.UserRole)]
                it.setCheckState(Qt.CheckState.Checked if on and pred(pin) else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self.update_summary()

    def since_dt(self) -> datetime | None:
        if not self.since_on.isChecked():
            return None
        return pt.parse_since(self.since.date().toString("yyyy-MM-dd"))

    def select_matching(self):
        since = self.since_dt()
        ok = {id(p) for p in pt.filter_since(self.pins, since)}
        self._check_all(True, lambda pin: id(pin) in ok)

    def checked_pins(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.flags() & Qt.ItemFlag.ItemIsUserCheckable and it.checkState() == Qt.CheckState.Checked:
                out.append(self.pins[it.data(Qt.ItemDataRole.UserRole)])
        return out

    def update_summary(self, *_):
        n = len(self.checked_pins())
        steps = self.step.currentData() or []
        gen = n * len(steps)
        text = f"선택 {n}장"
        if steps:
            per = self.estimate_fn(steps) if self.estimate_fn else None
            cost = per * n if per is not None else None
            text += f" · 생성 {gen}장 (유료)" + (f" · 예상 약 ${cost:.2f}" if cost is not None else " · 예상 비용 계산 불가")
        else:
            text += " · 생성 없음 (무료)"
        self.summary.setText(text)
        self.go_btn.setEnabled(n > 0)

    # ---- 가져오기 ---------------------------------------------------------
    def _target_project(self) -> int:
        name = self.project.currentText().strip() or "핀터레스트"
        p = self.ws.db.get_project_by_name(name)
        return p["id"] if p else self.ws.create_project(name)

    def do_import(self):
        pins = self.checked_pins()
        if not pins:
            return
        self.go_btn.setEnabled(False)
        self.load_btn.setEnabled(False)
        t = PinDownloader(pins, self.get, self)
        t.progress.connect(lambda i, n: self.status.setText(f"원본 받는 중… {i}/{n}"))
        t.done.connect(self._on_downloaded)
        self._threads.append(t)
        t.start()

    def _on_downloaded(self, ok, errors):
        board = pt.board_key(self.board.currentText())
        pid = self._target_project()
        ids = []
        for pin, data, url in ok:
            try:
                ids.append(self.ws.import_pin(pid, pin, data, url, board))
            except Exception as e:
                errors.append(f"{pin.pin_id}: {e}")
        self.imported_ids, self.project_id, self.steps = ids, pid, list(self.step.currentData() or [])
        if errors:
            QMessageBox.warning(self, "핀터레스트", f"{len(ids)}장 가져옴, {len(errors)}장 실패:\n" + "\n".join(errors[:8]))
        self.load_btn.setEnabled(True)
        if ids:
            self.accept()
        else:
            self.go_btn.setEnabled(True)

    def done(self, r):
        for t in self._threads:
            t.wait(3000)
        super().done(r)

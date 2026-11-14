"""화풍 프롬프트 추출 흐름 + 결과 대화상자 + 화풍 프리셋 관리 위젯."""
from __future__ import annotations

import json
import logging
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
                               QVBoxLayout, QWidget)

from .. import keystore
from ..services.image_service import GenerationError
from .workers import FuncWorker

log = logging.getLogger(__name__)


class StyleController(QObject):
    """화풍 추출 요청: 확인 → 백그라운드 호출 → 사용량 기록 → 결과 대화상자."""
    stylesChanged = Signal(int)   # 새로 저장된 화풍 ID
    usageChanged = Signal()

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.worker = None

    def is_busy(self):
        return self.worker is not None

    def start(self, image_row, parent_widget) -> bool:
        ws = self.ws
        if self.worker is not None:
            QMessageBox.information(parent_widget, "화풍 추출", "이미 진행 중인 화풍 추출이 있습니다.")
            return False
        if not ws.demo_mode and not keystore.get_key():
            QMessageBox.warning(parent_widget, "API 키 필요", "화풍 추출에도 OpenAI API 키가 필요합니다. [설정 > API]에서 등록하세요.")
            return False
        path = ws.image_path(image_row)
        if not path or not path.exists():
            QMessageBox.warning(parent_widget, "화풍 추출", "이미지 파일을 찾을 수 없습니다.")
            return False
        model = ws.style_model()
        tm = ws.catalog.text_model(model)
        if not ws.demo_mode and not self._confirm(model, parent_widget):
            return False
        instructions = ws.default_prompt("style_extract")
        service = ws.style_service
        service.reset_cancel()
        effort = tm.get("reasoning_effort")
        timeout = float(ws.settings.get("timeout_sec"))
        self._row, self._model, self._service = image_row, model, service
        self._progress = QProgressDialog("화풍을 분석하는 중… (보통 10~60초)", "취소", 0, 0, parent_widget)
        self._progress.setWindowTitle("화풍 추출")
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.canceled.connect(service.cancel)
        self._parent = parent_widget
        self._started = time.monotonic()
        self.worker = FuncWorker(lambda: service.extract(path, model, instructions, effort, timeout), self)
        self.worker.done.connect(self._ok)
        self.worker.error.connect(self._fail)
        self.worker.finished.connect(self._cleanup)
        log.info("style extract start: model=%s image=%s demo=%s", model, image_row["id"], ws.demo_mode)
        self.worker.start()
        self._progress.show()
        return True

    def _confirm(self, model, parent_widget) -> bool:
        ws = self.ws
        est = ws.catalog.text_estimate(model, ws.db)
        budget = float(ws.settings.get("monthly_budget_usd") or 0)
        month = ws.db.usage_summary(ws.month_start())
        over = budget > 0 and month["cost"] + (est.for_budget() or 0) > budget
        if not over and not ws.settings.get("confirm_before_request"):
            return True
        text = (f"이미지를 텍스트 모델({model})에 보내 화풍을 분석합니다. (유료, 이미지 생성 아님)\n\n{est.text()}")
        if over:
            text += f"\n\n⚠ 앱 내부 월간 소프트 예산(${budget:.2f})을 넘습니다. 이번 달 앱 추정 사용액 ${month['cost']:.3f}"
        ans = QMessageBox.question(parent_widget, "화풍 추출 요청 확인", text)
        return ans == QMessageBox.StandardButton.Yes

    def _log_usage(self, status, result=None, err=None):
        usage = result.usage if result else None
        cost = 0.0 if (result and result.demo) else (self.ws.catalog.text_cost(self._model, usage) if usage else None)
        self.ws.db.add_usage(
            project_id=self._row["project_id"], function="style_extract", model=self._model, quality="-", size="-",
            n_inputs=1, status=status, input_text_tokens=(usage or {}).get("input_tokens"),
            output_tokens=(usage or {}).get("output_tokens"), usage_json=json.dumps(usage) if usage else None,
            est_cost_usd=cost if status in ("ok", "demo") else None,
            request_id=(result.request_id if result else (err.request_id if err else None)),
            message=(f"[{err.kind}] {err.message}"[:500] if err else ("데모 모드" if result and result.demo else None)))
        self.usageChanged.emit()
        return cost

    def _ok(self, result):
        self._progress.reset()
        cost = self._log_usage("demo" if result.demo else "ok", result=result)
        dlg = StyleResultDialog(self.ws, result, self._row, cost, self._parent)
        dlg.exec()
        if dlg.saved_id:
            self.stylesChanged.emit(dlg.saved_id)

    def _fail(self, e):
        self._progress.reset()
        err = e if isinstance(e, GenerationError) else GenerationError("unknown", f"예상하지 못한 오류: {type(e).__name__}",
                                                                       billing_uncertain=True)
        status = "unknown" if err.billing_uncertain and not self.ws.demo_mode else "failed"
        self._log_usage(status, err=err)
        msg = err.message + (f"\n\n요청 ID: {err.request_id}" if err.request_id else "")
        QMessageBox.warning(self._parent, "화풍 추출 실패", msg)

    def _cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None

    def wait(self, msecs=5000):
        if self.worker is not None:
            self.worker.wait(msecs)


class StyleResultDialog(QDialog):
    def __init__(self, ws, result, image_row, cost, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.result = result
        self.image_row = image_row
        self.saved_id = None
        self.setWindowTitle("화풍 프롬프트 추출 결과")
        self.resize(760, 560)
        lay = QVBoxLayout(self)
        if result.demo:
            warn = QLabel("데모 모드 결과입니다. 실제 분석이 아닙니다.")
            warn.setStyleSheet("color: #b36b00; font-weight: bold;")
            lay.addWidget(warn)
        lay.addWidget(QLabel("요약 (한국어)"))
        summary = QPlainTextEdit(result.summary_ko or "(요약 없음)")
        summary.setReadOnly(True)
        summary.setFixedHeight(120)
        lay.addWidget(summary)
        lay.addWidget(QLabel("화풍 프롬프트 (영문, 직접 고칠 수 있습니다) — 생성 시 프롬프트 뒤에 붙여 쓰는 문장입니다"))
        self.prompt = QPlainTextEdit(result.prompt_en)
        lay.addWidget(self.prompt, 1)
        cost_text = "-" if cost is None else f"${cost:.5f}"
        info = QLabel(f"모델: {result.model} · 앱 추정 비용: {cost_text} · 요청 ID: {result.request_id or '-'}\n"
                      "저장하면 생성 화면의 [화풍] 목록에서 고를 수 있습니다. AI 분석이라 실제 화풍과 다를 수 있습니다.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #555;")
        lay.addWidget(info)
        row = QHBoxLayout()
        b = QPushButton("프롬프트 복사")
        b.clicked.connect(lambda: QApplication.clipboard().setText(self.prompt.toPlainText().strip()))
        row.addWidget(b)
        b = QPushButton("화풍 프리셋으로 저장…")
        b.clicked.connect(self.save)
        row.addWidget(b)
        row.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

    def save(self):
        default = f"{self.image_row['name'] or '화풍'} 화풍"
        name, ok = QInputDialog.getText(self, "화풍 프리셋 저장", "이름:", text=default)
        if not ok or not name.strip():
            return
        text = self.prompt.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "화풍", "프롬프트가 비어 있습니다.")
            return
        self.saved_id = self.ws.db.add_style(name.strip(), text, self.result.summary_ko, self.image_row["id"],
                                             self.result.model)
        QMessageBox.information(self, "화풍", "저장했습니다. 생성 화면의 [화풍] 목록에서 고르면 다음 요청부터 적용됩니다.")


class StylesManager(QWidget):
    """설정 > 화풍 프리셋 탭."""
    changed = Signal()

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.current = None
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(QLabel("저장된 화풍"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list, 1)
        b = QPushButton("새 화풍 (직접 입력)…")
        b.clicked.connect(self.new)
        left.addWidget(b)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        self.name = QLineEdit()
        right.addWidget(QLabel("이름"))
        right.addWidget(self.name)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: #555;")
        right.addWidget(self.summary)
        right.addWidget(QLabel("화풍 프롬프트 (영문) — 생성 화면에서 이 화풍을 고르면 모든 생성 프롬프트 뒤에 붙습니다"))
        self.prompt = QPlainTextEdit()
        right.addWidget(self.prompt, 1)
        row = QHBoxLayout()
        for text, fn in (("저장", self.save), ("복사", self.copy), ("삭제", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        right.addLayout(row)
        hint = QLabel("화풍은 생성 화면 하단 결과 메뉴의 [화풍 추출]로 이미지에서 뽑아 저장할 수 있습니다.")
        hint.setWordWrap(True)
        right.addWidget(hint)
        lay.addLayout(right, 3)
        self.reload()

    def reload(self, select_id=None):
        self.list.blockSignals(True)
        self.list.clear()
        target = 0
        for i, s in enumerate(self.ws.db.list_styles()):
            it = QListWidgetItem(s["name"])
            it.setData(Qt.ItemDataRole.UserRole, s["id"])
            self.list.addItem(it)
            if s["id"] == select_id:
                target = i
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(target)
        self._on_select()

    def _on_select(self, *_):
        it = self.list.currentItem()
        s = self.ws.db.get_style(it.data(Qt.ItemDataRole.UserRole)) if it else None
        self.current = s["id"] if s else None
        for w in (self.name, self.prompt):
            w.setEnabled(s is not None)
        self.name.setText(s["name"] if s else "")
        self.prompt.setPlainText(s["prompt"] if s else "")
        self.summary.setText(s["summary"] if s and s["summary"] else "")

    def new(self):
        name, ok = QInputDialog.getText(self, "새 화풍", "이름:")
        if ok and name.strip():
            sid = self.ws.db.add_style(name.strip(), "Describe the art style here.")
            self.reload(sid)
            self.changed.emit()

    def save(self):
        if not self.current:
            return
        if not self.prompt.toPlainText().strip():
            QMessageBox.warning(self, "화풍", "프롬프트가 비어 있습니다.")
            return
        self.ws.db.update_style(self.current, name=self.name.text().strip() or "이름 없음",
                                prompt=self.prompt.toPlainText().strip())
        self.reload(self.current)
        self.changed.emit()

    def copy(self):
        QApplication.clipboard().setText(self.prompt.toPlainText().strip())

    def delete(self):
        if self.current and QMessageBox.question(self, "화풍", "이 화풍 프리셋을 삭제할까요?") == QMessageBox.StandardButton.Yes:
            self.ws.db.delete_style(self.current)
            if self.ws.settings.get("last_style_id") == self.current:
                self.ws.settings.set("last_style_id", None)
            self.reload()
            self.changed.emit()


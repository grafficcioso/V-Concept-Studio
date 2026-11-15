"""생성 요청 흐름: 검증 → 예상 비용/예산 확인 → QThread 로 API 호출 → 저장/사용량 기록.

생성 탭과 조합 탭(AI 조합)이 같이 쓴다. 실패 시 자동 재시도는 하지 않는다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QCheckBox, QMessageBox

from .. import jobs as jobs_core
from .. import keystore
from ..jobs import GenJob  # noqa: F401  (예전 import 경로 유지)
from ..functions import label_of
from ..services.image_service import GenerationError, GenerationRequest, GenerationResult

log = logging.getLogger(__name__)


class GenerationWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, service, request: GenerationRequest, parent=None):
        super().__init__(parent)
        self.service = service
        self.request = request

    def run(self):
        try:
            self.succeeded.emit(self.service.generate(self.request))
        except GenerationError as e:
            self.failed.emit(e)
        except Exception as e:  # 워커에서 앱이 죽지 않도록
            log.exception("generation worker crashed")
            self.failed.emit(GenerationError("unknown", f"예상하지 못한 오류: {type(e).__name__}", detail=repr(e)[:500],
                                             billing_uncertain=True))


class GenerationController(QObject):
    started = Signal(object)          # GenJob
    finished = Signal(int, object)    # image_id, GenJob
    failed = Signal(str, object)      # message, GenJob
    usageChanged = Signal()

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.worker: GenerationWorker | None = None
        self.job: GenJob | None = None
        self._service = None

    def is_busy(self) -> bool:
        return self.worker is not None

    # ---- 시작 -----------------------------------------------------------
    def start(self, job: GenJob, parent_widget) -> bool:
        ws = self.ws
        if self.is_busy():
            QMessageBox.information(parent_widget, "생성 중", "이미 진행 중인 요청이 있습니다. 끝난 뒤 다시 시도하세요.")
            return False
        if not ws.demo_mode and not keystore.get_key():
            QMessageBox.warning(parent_widget, "API 키 필요",
                                "OpenAI API 키가 등록되어 있지 않습니다.\n[설정 > API]에서 키를 등록한 뒤 생성할 수 있습니다.\n\n"
                                "키 없이도 이미지 관리·라이브러리·조합 기능은 사용할 수 있습니다.")
            return False
        if not job.input_paths or not all(Path(p).exists() for p in job.input_paths):
            QMessageBox.warning(parent_widget, "입력 이미지 필요", "입력 이미지가 없거나 파일을 찾을 수 없습니다.")
            return False
        if not job.model:
            QMessageBox.warning(parent_widget, "모델 필요", "모델을 선택하세요.")
            return False
        if not job.prompt.strip():
            QMessageBox.warning(parent_widget, "프롬프트 필요", "프롬프트가 비어 있습니다. [설정 > 프롬프트]에서 확인하세요.")
            return False
        problems = ws.catalog.validate(job.model, job.quality, job.size, job.background)
        if problems:
            QMessageBox.warning(parent_widget, "지원되지 않는 옵션", "\n".join(problems))
            return False
        if job.markup_path and not ws.catalog.get(job.model).get("markup"):
            ok_models = ", ".join(ws.catalog.markup_models()) or "(요금표에 지원 모델 없음)"
            QMessageBox.warning(parent_widget, "영역 지정 수정 미지원",
                                f"선택한 모델({job.model})은 빨간 펜 영역 지정(markup)을 지원하지 않습니다.\n"
                                f"생성 화면에서 모델을 바꿔 주세요. 지원 모델: {ok_models}\n\n"
                                "또는 칠한 영역을 모두 지우면 이미지 전체에 대한 수정 요청으로 보낼 수 있습니다.")
            return False
        if not self._confirm(job, parent_widget):
            return False

        self.job = job
        self._service = ws.service
        self._service.reset_cancel()
        req = jobs_core.make_request(ws, job)
        self.worker = GenerationWorker(self._service, req, self)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.finished.connect(self._cleanup)
        log.info("generate start: function=%s model=%s quality=%s size=%s bg=%s inputs=%d demo=%s",
                 job.function, job.model, job.quality, job.size, job.background, len(job.input_paths), ws.demo_mode)
        self.worker.start()
        self.started.emit(job)
        return True

    def _confirm(self, job: GenJob, parent_widget) -> bool:
        ws = self.ws
        if ws.demo_mode:
            return True
        est = ws.catalog.estimate(job.model, job.quality, job.size, job.prompt, len(job.input_paths), ws.db)
        budget = float(ws.settings.get("monthly_budget_usd") or 0)
        month = ws.db.usage_summary(ws.month_start())
        over = False
        budget_text = ""
        if budget > 0:
            projected = month["cost"] + (est.for_budget() or 0)
            over = projected > budget
            budget_text = (f"\n\n이번 달 앱 추정 사용액: ${month['cost']:.3f} / 소프트 예산 ${budget:.2f}"
                           + (f"\n→ 이 요청 후 예상: ${projected:.3f}" if est.computable else ""))
            if over:
                budget_text += "\n⚠ 앱 내부 월간 소프트 예산을 넘습니다."
            if month["unknown"]:
                budget_text += f"\n(과금 여부 불명 요청 {month['unknown']}건은 합계에 포함되지 않았습니다.)"
        if not over and not ws.settings.get("confirm_before_request"):
            return True
        box = QMessageBox(parent_widget)
        box.setIcon(QMessageBox.Icon.Warning if over else QMessageBox.Icon.Question)
        box.setWindowTitle("유료 생성 요청 확인")
        box.setText(f"[{label_of(job.function)}] 요청을 OpenAI API로 보냅니다. (유료)\n\n"
                    f"모델: {job.model}\n품질: {job.quality} · 크기: {job.size} · 배경: {job.background}\n"
                    f"참조 이미지: {len(job.input_paths)}장" + (" · 빨간 펜 영역 지정 있음" if job.markup_path else ""))
        box.setInformativeText(est.text() + budget_text +
                               "\n\n예산은 이 앱의 로컬 추정치에 근거한 소프트 제한이며, OpenAI 계정의 청구를 차단하지 않습니다.")
        go = box.addButton("생성", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        skip = None
        if not over:
            skip = QCheckBox("다음부터 묻지 않기 (예산 초과 시에는 항상 확인)")
            box.setCheckBox(skip)
        box.exec()
        if box.clickedButton() is not go:
            return False
        if skip is not None and skip.isChecked():
            ws.settings.set("confirm_before_request", False)
        return True

    def cancel(self):
        if self.worker is not None and self._service is not None:
            self._service.cancel()

    # ---- 결과 -----------------------------------------------------------
    def _usage_fields(self, job: GenJob):
        return dict(project_id=job.project_id, function=job.function, model=job.model, quality=job.quality,
                    size=job.size, n_inputs=len(job.input_paths))

    def _on_success(self, result: GenerationResult):
        job = self.job
        try:
            image_id = jobs_core.record_success(self.ws, job, result)
        except GenerationError as e:
            self.usageChanged.emit()
            self.failed.emit(e.message, job)
            return
        self.usageChanged.emit()
        self.finished.emit(image_id, job)

    def _on_failure(self, err: GenerationError):
        job = self.job
        msg = jobs_core.record_failure(self.ws, job, err)
        self.usageChanged.emit()
        self.failed.emit(msg, job)

    def _cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        self._service = None

    def wait(self, msecs=5000):
        if self.worker is not None:
            self.worker.wait(msecs)

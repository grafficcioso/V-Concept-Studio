"""사용량 · 비용(앱 추정) 화면."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..functions import label_of

STATUS = {"ok": "성공", "failed": "실패(과금 없음 추정)", "unknown": "과금 여부 불명", "demo": "데모"}
COLS = ["시각", "기능", "모델", "품질", "크기", "입력", "상태", "입력 토큰(텍스트/이미지)", "출력 토큰", "앱 추정 비용",
        "요청 ID", "비고"]


class UsageTab(QWidget):
    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        lay = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-size: 14px;")
        lay.addWidget(self.summary)
        note = QLabel("표시된 금액은 API가 돌려준 사용량(토큰)과 [설정 > 모델·요금표]의 단가로 계산한 이 앱의 추정치입니다. "
                      "OpenAI의 실제 청구액이 아니며, 다른 앱에서 쓴 금액은 포함되지 않습니다. 정확한 금액은 OpenAI 대시보드의 Usage에서 확인하세요.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        lay.addWidget(note)
        row = QHBoxLayout()
        b = QPushButton("새로고침")
        b.clicked.connect(self.reload)
        row.addWidget(b)
        b = QPushButton("OpenAI 결제(잔액) 페이지 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://platform.openai.com/settings/organization/billing/overview")))
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.reload()

    def reload(self):
        ws = self.ws
        day = ws.db.usage_summary(ws.day_start())
        month = ws.db.usage_summary(ws.month_start())
        budget = float(ws.settings.get("monthly_budget_usd") or 0)
        lines = [f"오늘: 성공 {day['ok']}건 · 앱 추정 ${day['cost']:.3f}",
                 f"이번 달: 성공 {month['ok']}건 · 앱 추정 ${month['cost']:.3f}"
                 + (f" / 소프트 예산 ${budget:.2f} ({month['cost'] / budget * 100:.0f}%)" if budget > 0
                    else " (월간 소프트 예산 미설정)")]
        extra = []
        if month["ok_uncosted"]:
            extra.append(f"비용 계산 불가 {month['ok_uncosted']}건(합계 미포함)")
        if month["unknown"]:
            extra.append(f"과금 여부 불명 {month['unknown']}건(합계 미포함)")
        if month["failed"]:
            extra.append(f"실패 {month['failed']}건")
        if extra:
            lines.append("이번 달 기타: " + " · ".join(extra))
        c = ws.credit_status()
        if c:
            lines.append(f"충전 ${c['total']:.2f} (기준일 {c['since'] or '전체'}) − 앱 기록 사용 ${c['used']:.3f}"
                         f" = 추정 잔액 ${c['remaining']:.3f}")
        else:
            lines.append("충전액 미설정 — [설정 > 예산 · 충전액]에서 입력하면 추정 잔액을 보여 줍니다.")
        self.summary.setText("\n".join(lines))
        rows = ws.db.usage_rows()
        self.table.setRowCount(len(rows))
        for r, u in enumerate(rows):
            tok_in = "-" if u["input_text_tokens"] is None and u["input_image_tokens"] is None \
                else f"{u['input_text_tokens'] or 0} / {u['input_image_tokens'] or 0}"
            cost = "-" if u["est_cost_usd"] is None else f"${u['est_cost_usd']:.4f}"
            vals = [u["created_at"].replace("T", " "), label_of(u["function"]), u["model"] or "", u["quality"] or "",
                    u["size"] or "", f"{u['n_inputs']}장", STATUS.get(u["status"], u["status"]), tok_in,
                    "-" if u["output_tokens"] is None else str(u["output_tokens"]), cost, u["request_id"] or "",
                    (u["message"] or "").replace("\n", " ")]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))

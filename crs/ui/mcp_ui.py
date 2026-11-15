"""설정 → Claude 연결 (MCP 모드) 탭."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import EXE_NAME


def mcp_command() -> str:
    """Claude Code 에 등록할 명령 (사용자 범위: 어느 폴더에서 켜도 사용 가능)."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).with_name(f"{EXE_NAME}-mcp.exe")
        target = f'"{exe}"'
    else:
        root = Path(__file__).resolve().parents[2]
        target = f'"{sys.executable}" "{root / "mcp_main.py"}"'
    return f"claude mcp add --scope user v-concept -- {target}"


class McpSettingsWidget(QWidget):
    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        s = ws.settings
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Claude Code 가 이 프로그램의 기능(정면화·파트 분할·사면도·홍채·업스케일, 받은 원본 확인, 이름 정리, 동기화)을 "
            "도구로 쓸 수 있게 합니다. Claude Code 의 원격 제어(Remote Control)와 함께 쓰면 밖에서 폰의 Claude 앱으로 "
            "\"받은 원본 사면도까지 뽑아 둬\"처럼 시킬 수 있습니다.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self.enabled = QCheckBox("Claude Code 연결 허용 (MCP 모드)")
        self.enabled.setChecked(bool(s.get("mcp_enabled")))
        self.enabled.toggled.connect(lambda v: s.set("mcp_enabled", bool(v)))
        lay.addWidget(self.enabled)

        form = QFormLayout()
        self.max_images = QSpinBox()
        self.max_images.setRange(1, 50)
        self.max_images.setValue(int(s.get("mcp_max_images_per_call") or 6))
        self.max_images.setSuffix(" 장")
        self.max_images.valueChanged.connect(lambda v: s.set("mcp_max_images_per_call", int(v)))
        form.addRow("요청 1번 최대 장수", self.max_images)
        self.daily = QDoubleSpinBox()
        self.daily.setRange(0, 500)
        self.daily.setDecimals(2)
        self.daily.setPrefix("$ ")
        self.daily.setValue(float(s.get("mcp_daily_budget_usd") or 0))
        self.daily.valueChanged.connect(lambda v: s.set("mcp_daily_budget_usd", float(v)))
        form.addRow("Claude 연결 하루 상한 (추정액)", self.daily)
        lay.addLayout(form)
        guard = QLabel("Claude 연결로 하는 생성은 확인 창 없이 바로 보내집니다. 대신 위 한도를 넘는 요청은 거절합니다 "
                       "(0 이면 유료 생성 금지). 금액은 이 앱의 추정치이며 OpenAI 청구를 직접 막지는 않습니다. "
                       "월간 예산을 정해 두었다면 그것도 지킵니다.")
        guard.setWordWrap(True)
        guard.setStyleSheet("color:#8a5a00")
        lay.addWidget(guard)

        lay.addWidget(QLabel("<b>1) PC의 명령 프롬프트(또는 PowerShell)에 붙여 넣어 Claude Code 에 등록</b>"))
        row = QHBoxLayout()
        self.cmd = QLineEdit(mcp_command())
        self.cmd.setReadOnly(True)
        row.addWidget(self.cmd, 1)
        copy = QPushButton("복사")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.cmd.text()))
        row.addWidget(copy)
        lay.addLayout(row)
        steps = QLabel(
            "<b>2) 밖에서 폰으로 시키려면</b> — PC에서 <code>claude remote-control</code> 을 실행해 두고, "
            "폰의 Claude 앱 → <b>Code</b> 탭에서 초록 점이 붙은 내 PC 세션을 엽니다. "
            "PC는 켜 둔 채(절전 모드 '안 함') 두어야 합니다.<br>"
            "<b>3) 예시</b> — \"받은 원본 확인하고 메모대로 사면도까지 뽑아 줘\", "
            "\"캐릭터A 사면도 결과를 D:\\정리 폴더에 '캐릭터A_사면도_01' 식으로 복사해 줘\"<br>"
            "<b>참고</b> — 프로그램을 업데이트하면 Claude Code 연결이 끊깁니다. Claude Code 에서 <code>/mcp</code> 로 다시 연결하세요. "
            "프로그램 창을 켜 두지 않아도 동작하며, 켜 두면 Claude 가 만든 결과가 몇 초 안에 목록에 나타납니다.")
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(steps)
        lay.addStretch(1)

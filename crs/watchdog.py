"""GUI 멈춤 감시: 메인 스레드가 일정 시간 이상 응답하지 않으면 모든 스레드의 위치를 logs/hang.log 에 남긴다.

사용자 PC에서만 생기는 '응답 없음' 원인을 찾기 위한 진단 장치다. 동작에는 영향을 주지 않는다.
"""
from __future__ import annotations

import faulthandler
import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer


class GuiWatchdog(QObject):
    def __init__(self, log_dir: Path, stall_sec: float = 8.0, parent=None):
        super().__init__(parent)
        self.path = Path(log_dir) / "hang.log"
        self.stall_sec = stall_sec
        self._beat = time.monotonic()
        self._reported = False
        self._stop = threading.Event()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        self._thread = threading.Thread(target=self._watch, name="gui-watchdog", daemon=True)
        self._thread.start()

    def _tick(self):
        self._beat = time.monotonic()
        if self._reported:
            self._reported = False
            self._write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 응답 회복\n")

    def _write(self, text: str):
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def _watch(self):
        while not self._stop.wait(2.0):
            stalled = time.monotonic() - self._beat
            if stalled > self.stall_sec and not self._reported:
                self._reported = True
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 화면이 {stalled:.0f}초 동안 응답하지 않음 =====\n")
                        f.flush()
                        faulthandler.dump_traceback(file=f, all_threads=True)
                except OSError:
                    pass

    def stop(self):
        self._stop.set()
        self._timer.stop()

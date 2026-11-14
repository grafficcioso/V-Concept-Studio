"""백그라운드 실행용 범용 QThread."""
from __future__ import annotations

import logging

from PySide6.QtCore import QThread, Signal

log = logging.getLogger(__name__)


class FuncWorker(QThread):
    done = Signal(object)
    error = Signal(object)
    progress = Signal(int, int)

    def __init__(self, fn, parent=None, with_progress=False):
        super().__init__(parent)
        self.fn = fn
        self.with_progress = with_progress

    def run(self):
        try:
            result = self.fn(lambda a, b: self.progress.emit(a, b)) if self.with_progress else self.fn()
            self.done.emit(result)
        except Exception as e:
            log.info("worker error: %s", type(e).__name__)
            self.error.emit(e)

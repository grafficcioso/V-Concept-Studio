"""동기화 동시 실행 방지 (프로그램 창 ↔ MCP 모드, 서로 다른 프로세스)."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path


class SyncBusy(Exception):
    pass


@contextmanager
def sync_lock(root: Path, wait_ms: int = 90_000):
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(Path(root) / "sync.lock"))
    lock.setStaleLockTime(15 * 60 * 1000)     # 15분 넘게 잡혀 있으면 멈춘 것으로 보고 넘겨받는다
    if not lock.tryLock(wait_ms):
        raise SyncBusy("다른 곳(프로그램 창 또는 Claude 연결)에서 동기화 중입니다. 잠시 뒤 다시 시도하세요.")
    try:
        yield
    finally:
        lock.unlock()

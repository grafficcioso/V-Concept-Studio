"""GUI 멈춤 감시: 메인 스레드가 일정 시간 이상 응답하지 않으면 모든 스레드의 위치를 logs/hang.log 에 남긴다.

사용자 PC에서만 생기는 '응답 없음' 원인을 찾기 위한 진단 장치다. 동작에는 영향을 주지 않는다.
"""
from __future__ import annotations

import faulthandler
import sys
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
        self._dumps = 0
        self.max_dumps = 3
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
                if sys.platform == "win32" and self._dumps < self.max_dumps:
                    self._dumps += 1
                    dmp = self.path.parent / f"hang_{datetime.now():%Y%m%d_%H%M%S}.dmp"
                    ok = write_minidump(dmp)
                    self._write(f"덤프 파일: {dmp.name if ok else '(만들지 못함)'}\n")

    def stop(self):
        self._stop.set()
        self._timer.stop()


def write_minidump(path: Path) -> bool:
    """현재 프로세스의 스레드 스택을 담은 작은 미니덤프(.dmp)를 만든다 (Windows 전용, 진단용).

    스레드 스택과 모듈 목록만 담는 종류를 써서 크기가 작고, 이미지 데이터 같은 큰 메모리는 담지 않는다.
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        dbg = ctypes.WinDLL("dbghelp", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcessId.restype = wintypes.DWORD
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        dbg.MiniDumpWriteDump.restype = wintypes.BOOL
        dbg.MiniDumpWriteDump.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD,
                                          wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
        handle = k32.CreateFileW(str(path), 0x40000000, 0, None, 2, 0x80, None)  # GENERIC_WRITE, CREATE_ALWAYS
        if handle in (None, wintypes.HANDLE(-1).value):
            return False
        try:
            # MiniDumpNormal | MiniDumpWithUnloadedModules | MiniDumpWithThreadInfo
            kind = 0x0 | 0x20 | 0x1000
            return bool(dbg.MiniDumpWriteDump(k32.GetCurrentProcess(), k32.GetCurrentProcessId(), handle, kind,
                                              None, None, None))
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return False

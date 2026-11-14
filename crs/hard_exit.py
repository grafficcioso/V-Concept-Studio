"""Windows 에서 Qt 정리 단계의 종료 시 충돌을 피하는 즉시 종료.

일반 종료(os._exit 포함)는 DLL 정리를 거치는데, 이때 Qt 가 접근 위반을 내며
'프로그램이 중지되었습니다' 창이 뜰 수 있다. 데이터는 이미 저장/정리된 상태에서만 호출한다.
"""
from __future__ import annotations

import logging
import os
import sys


def hard_exit(code: int = 0):
    try:
        logging.shutdown()
        sys.stdout and sys.stdout.flush()
        sys.stderr and sys.stderr.flush()
    except Exception:
        pass
    if sys.platform == "win32":
        import ctypes
        try:
            # 앱이 넣어 둔 클립보드 내용(복사한 이미지)을 시스템에 넘겨, 종료 후에도 붙여넣기가 되게 한다.
            ctypes.windll.ole32.OleFlushClipboard()
        except Exception:
            pass
        k32 = ctypes.windll.kernel32
        k32.TerminateProcess(k32.GetCurrentProcess(), int(code))
    os._exit(int(code))

"""Windows 진단: 업데이트 완료 창을 닫았을 때 앱이 멈추는지 재현한다 (CI 전용)."""
import faulthandler
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

faulthandler.dump_traceback_later(60, exit=True)   # 멈추면 모든 스레드의 위치를 출력하고 종료
root = Path(tempfile.gettempdir()) / "vcs_diag"
shutil.rmtree(root, ignore_errors=True)
os.environ["CRS_CONFIG_DIR"] = str(root / "cfg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crs.config import Settings  # noqa: E402

s = Settings()
s.set("data_root", str(root / "data"))
s.set("last_run_version", sys.argv[1] if len(sys.argv) > 1 else "0.2.0")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import crs.app as appmod  # noqa: E402

state = {"closed": 0, "t0": time.monotonic()}


def tick():
    app = QApplication.instance()
    w = app.activeModalWidget()
    print(f"{time.monotonic() - state['t0']:6.1f}s modal={type(w).__name__ if w else None} "
          f"title={w.windowTitle() if w else ''}", flush=True)
    if w is not None and state["closed"] < 3:
        state["closed"] += 1
        print("  -> closing via close()", flush=True)
        w.close()


orig = QApplication.exec


def patched():
    t = QTimer()
    t.timeout.connect(tick)
    t.start(1000)
    QTimer.singleShot(20000, lambda: (print("event loop alive at 20s -> quit", flush=True),
                                      QApplication.instance().activeWindow() and None,
                                      QApplication.instance().quit()))
    return orig()


QApplication.exec = patched
os.environ.pop("CRS_SMOKE_QUIT_MS", None)
rc = appmod.main()
print("main returned", rc, flush=True)
log = root / "data" / "logs" / "app.log"
print(log.read_text(encoding="utf-8") if log.exists() else "no log", flush=True)

"""앱 진입점."""
from __future__ import annotations

import logging
import os
import sys
import traceback


def _debug_dump():
    """진단용: CRS_DEBUG_DUMP=초 가 있으면 그 시간 뒤 모든 스레드 위치를 파일에 기록 (EXE 는 콘솔이 없어서)."""
    sec = os.environ.get("CRS_DEBUG_DUMP")
    path = os.environ.get("CRS_DEBUG_DUMP_FILE")
    if sec and path:
        import faulthandler
        fh = open(path, "w", encoding="utf-8")  # noqa: SIM115 - 프로세스 끝까지 열어 둔다
        faulthandler.dump_traceback_later(int(sec), repeat=True, file=fh)
        return fh
    return None


def _make_app_class():
    """이벤트 하나를 처리하는 데 1초 넘게 걸리면 무엇이었는지 hang.log 에 남기는 QApplication (진단용)."""
    import time

    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    class TimedApplication(QApplication):
        slow_log = None   # Path, 워크스페이스가 열린 뒤 설정

        def notify(self, receiver, event):
            t0 = time.perf_counter()
            try:
                return super().notify(receiver, event)
            finally:
                dt = time.perf_counter() - t0
                if dt > 1.0 and self.slow_log is not None:
                    self._report(receiver, event, dt)

        def _report(self, receiver, event, dt):
            try:
                etype = event.type()
                name = QEvent.Type(etype).name if hasattr(QEvent.Type(etype), "name") else str(int(etype))
            except Exception:
                name = "?"
            chain, obj = [], receiver
            try:
                while obj is not None and len(chain) < 6:
                    label = type(obj).__name__
                    if obj.objectName():
                        label += f"({obj.objectName()})"
                    if hasattr(obj, "windowTitle") and obj.windowTitle():
                        label += f"['{obj.windowTitle()[:30]}']"
                    chain.append(label)
                    obj = obj.parent()
            except Exception:
                pass
            from datetime import datetime
            try:
                with open(self.slow_log, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} 느린 이벤트 {dt:.1f}초: {name} → {' < '.join(chain)}\n")
            except OSError:
                pass

    return TimedApplication


def main() -> int:
    dump_fh = _debug_dump()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QMessageBox

    from . import APP_NAME, APP_TITLE
    from .logging_setup import redact

    app = _make_app_class()(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    if sys.platform == "win32":
        app.setFont(QFont("Malgun Gothic", 9))

    def excepthook(etype, value, tb):
        # 예상하지 못한 오류로 앱이 종료되지 않도록 하고, 민감 정보 없이 로그에 남긴다.
        text = redact("".join(traceback.format_exception(etype, value, tb)))
        logging.getLogger("crs").error("unhandled exception:\n%s", text)
        try:
            QMessageBox.critical(None, "오류", f"예상하지 못한 오류가 발생했습니다.\n{etype.__name__}: {redact(str(value))}\n\n"
                                 "자세한 내용은 데이터 폴더의 logs/app.log 를 확인하세요.")
        except Exception:
            pass

    sys.excepthook = excepthook

    try:
        from .workspace import Workspace
        ws = Workspace()
    except Exception as e:
        QMessageBox.critical(None, "시작 오류", f"데이터 폴더를 열 수 없습니다.\n{type(e).__name__}: {e}\n\n"
                             "설정 파일(settings.json)의 data_root 경로를 확인하세요.")
        return 1

    # 중복 실행 방지: 같은 데이터·설정 파일을 두 프로그램이 동시에 쓰지 않도록 한다.
    from PySide6.QtCore import QLockFile
    from .config import config_dir
    lock = QLockFile(str(config_dir() / "instance.lock"))
    lock.setStaleLockTime(0)   # 이전 프로그램이 비정상 종료했으면(프로세스 없음) 자동으로 풀린다
    if not lock.tryLock(1500):
        QMessageBox.information(None, "이미 실행 중", "V-Concept Studio 가 이미 실행 중입니다.\n"
                                "작업 표시줄에서 열려 있는 창을 찾아 주세요.\n\n"
                                "창이 응답하지 않는다면 작업 관리자에서 VConceptStudio 를 끝낸 뒤 다시 실행하세요.")
        ws.close()
        return 0
    app.slow_log = ws.paths.logs / "hang.log"

    from .ui.main_window import MainWindow
    win = MainWindow(ws)
    win.show()
    win.start_background_tasks(sys.argv)
    smoke = os.environ.get("CRS_SMOKE_QUIT_MS")  # 빌드 확인용: 지정한 시간 뒤 창을 닫아 정상 종료 코드를 확인
    if smoke:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(smoke), win.close)
    rc = app.exec()
    lock.unlock()
    if dump_fh is not None:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
        dump_fh.close()
    # 창이 닫힐 때 DB·설정은 이미 저장/정리되었다. Windows 에서 Qt 정리 단계의 종료 시 충돌
    # ('프로그램이 중지되었습니다' 창)을 막기 위해 곧바로 프로세스를 끝낸다.
    if sys.platform == "win32":
        from .hard_exit import hard_exit
        hard_exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())

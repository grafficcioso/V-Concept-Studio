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


def main() -> int:
    dump_fh = _debug_dump()
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import APP_NAME, APP_TITLE
    from .logging_setup import redact

    app = QApplication(sys.argv)
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

    from .ui.main_window import MainWindow
    win = MainWindow(ws)
    win.show()
    win.start_background_tasks(sys.argv)
    smoke = os.environ.get("CRS_SMOKE_QUIT_MS")  # 빌드 확인용: 지정한 시간 뒤 창을 닫아 정상 종료 코드를 확인
    if smoke:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(int(smoke), win.close)
    rc = app.exec()
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

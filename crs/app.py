"""앱 진입점."""
from __future__ import annotations

import logging
import sys
import traceback


def main() -> int:
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
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

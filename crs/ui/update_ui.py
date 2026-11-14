"""업데이트 안내 / 다운로드 진행 / 변경 내용 대화상자."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog,
                               QPushButton, QTextBrowser, QVBoxLayout)

from .. import APP_VERSION, updater
from .workers import FuncWorker

log = logging.getLogger(__name__)


class ChangelogDialog(QDialog):
    def __init__(self, parent=None, version: str | None = None, title: str | None = None, intro: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title or "변경 내역")
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        if intro:
            head = QLabel(intro)
            head.setWordWrap(True)
            head.setStyleSheet("font-weight: bold; font-size: 14px;")
            lay.addWidget(head)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        text = updater.changelog_section(version) if version else updater.changelog_text()
        view.setMarkdown(text or "변경 내역이 없습니다.")
        lay.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)


class UpdateDialog(QDialog):
    """새 버전 안내: 업데이트 / 나중에 / 이 버전 건너뛰기."""
    UPDATE, LATER, SKIP = 1, 2, 3

    def __init__(self, info: updater.ReleaseInfo, can_install: bool, reason: str = "", parent=None):
        super().__init__(parent)
        self.choice = self.LATER
        self.setWindowTitle("업데이트 알림")
        self.resize(620, 520)
        lay = QVBoxLayout(self)
        head = QLabel(f"새 버전 v{info.version} 이 있습니다. (현재 v{APP_VERSION})\n지금 업데이트할까요?")
        head.setStyleSheet("font-weight: bold; font-size: 14px;")
        lay.addWidget(head)
        lay.addWidget(QLabel("바뀐 점"))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(info.notes or "(릴리스 설명 없음)")
        lay.addWidget(notes, 1)
        size = f" · 내려받을 크기 약 {info.zip_size / 1024 / 1024:.0f}MB" if info.zip_size else ""
        tail = QLabel("업데이트하면 프로그램이 잠시 꺼졌다가 새 버전으로 다시 켜집니다. "
                      "프로젝트·라이브러리·프롬프트·API 키는 그대로 유지됩니다." + size)
        tail.setWordWrap(True)
        lay.addWidget(tail)
        if not can_install:
            why = QLabel("자동 설치를 할 수 없습니다: " + reason + "\n[다운로드 페이지 열기]로 직접 받아 주세요.")
            why.setWordWrap(True)
            why.setStyleSheet("color: #b3261e;")
            lay.addWidget(why)
        box = QDialogButtonBox()
        if can_install:
            go = QPushButton("업데이트")
            go.setDefault(True)
            go.clicked.connect(lambda: self._done(self.UPDATE))
        else:
            go = QPushButton("다운로드 페이지 열기")
            go.clicked.connect(lambda: (QDesktopServices.openUrl(QUrl(info.html_url)), self._done(self.LATER)))
        box.addButton(go, QDialogButtonBox.ButtonRole.AcceptRole)
        later = QPushButton("나중에")
        later.clicked.connect(lambda: self._done(self.LATER))
        box.addButton(later, QDialogButtonBox.ButtonRole.RejectRole)
        skip = QPushButton("이 버전 건너뛰기")
        skip.clicked.connect(lambda: self._done(self.SKIP))
        box.addButton(skip, QDialogButtonBox.ButtonRole.DestructiveRole)
        lay.addWidget(box)

    def _done(self, choice):
        self.choice = choice
        self.accept()


class UpdateManager(QObject):
    """시작 시 확인, 안내, 다운로드, 설치 실행을 묶는다."""
    quitRequested = Signal()

    def __init__(self, ws, window, is_busy=lambda: False):
        super().__init__(window)
        self.ws = ws
        self.window = window
        self.is_busy = is_busy
        self.worker = None
        self._cancel = False

    def check_on_startup(self, delay_ms=1500):
        if self.ws.settings.get("check_updates"):
            QTimer.singleShot(delay_ms, lambda: self.check(silent=True))

    def check(self, silent=True):
        if self.worker is not None:
            return
        skipped = self.ws.settings.get("skipped_version") if silent else ""
        self.worker = FuncWorker(lambda: updater.check_latest(skipped=skipped), self)
        self.worker.done.connect(lambda info: self._on_checked(info, silent))
        self.worker.error.connect(lambda e: self._on_check_error(e, silent))
        self.worker.finished.connect(self._clear)
        self.worker.start()

    def _clear(self):
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None

    def _on_check_error(self, e, silent):
        log.info("update check failed: %s", e)
        if not silent:
            QMessageBox.information(self.window, "업데이트 확인", f"업데이트를 확인하지 못했습니다: {e}")

    def _on_checked(self, info, silent):
        if info is None:
            if not silent:
                QMessageBox.information(self.window, "업데이트 확인", f"최신 버전(v{APP_VERSION})을 사용 중입니다.")
            return
        self.offer(info)

    def offer(self, info: updater.ReleaseInfo):
        target = updater.install_dir()
        reason = ""
        if target is None:
            reason = "소스 코드로 실행 중입니다"
        elif not updater.is_writable(target):
            reason = "프로그램 폴더에 쓸 권한이 없습니다(예: Program Files)"
        elif not info.zip_url:
            reason = "릴리스에 설치 파일이 없습니다"
        dlg = UpdateDialog(info, can_install=not reason, reason=reason, parent=self.window)
        dlg.exec()
        if dlg.choice == UpdateDialog.SKIP:
            self.ws.settings.set("skipped_version", info.version)
        elif dlg.choice == UpdateDialog.UPDATE:
            self.install(info)

    def install(self, info: updater.ReleaseInfo):
        if self.is_busy():
            QMessageBox.information(self.window, "업데이트", "생성 요청이 진행 중입니다. 끝난 뒤 설정 > 업데이트에서 다시 시도하세요.")
            return
        self._cancel = False
        progress = QProgressDialog(f"v{info.version} 내려받는 중…", "취소", 0, 100, self.window)
        progress.setWindowTitle("업데이트")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(lambda: setattr(self, "_cancel", True))
        self._dl = FuncWorker(lambda report: updater.prepare_update(info, report, lambda: self._cancel), self,
                              with_progress=True)

        def on_progress(done, total):
            if total:
                progress.setValue(int(done * 100 / total))
                progress.setLabelText(f"v{info.version} 내려받는 중… {done / 1048576:.0f} / {total / 1048576:.0f}MB")

        def on_done(script):
            progress.setValue(100)
            progress.close()
            QMessageBox.information(self.window, "업데이트", "준비가 끝났습니다. 프로그램을 닫고 새 버전으로 다시 실행합니다.")
            try:
                updater.launch_update_script(script)
            except OSError as e:
                QMessageBox.warning(self.window, "업데이트", f"업데이트를 시작하지 못했습니다: {e}")
                return
            self.quitRequested.emit()

        def on_error(e):
            progress.close()
            if not self._cancel:
                QMessageBox.warning(self.window, "업데이트 실패", f"{e}\n\n릴리스 페이지: {info.html_url}")

        self._dl.progress.connect(on_progress)
        self._dl.done.connect(on_done)
        self._dl.error.connect(on_error)
        self._dl.start()
        progress.show()


def show_post_update(ws, window, argv) -> None:
    """버전이 바뀐 뒤 처음 실행되면 바뀐 점을 보여 준다."""
    last = ws.settings.get("last_run_version") or ""
    if "--update-failed" in argv:
        QMessageBox.warning(window, "업데이트 실패", "새 버전 파일을 복사하지 못해 이전 버전으로 실행했습니다.\n"
                            "잠시 뒤 다시 시도하거나 릴리스 페이지에서 직접 받아 주세요.")
    if last and updater.parse_version(last) < updater.parse_version(APP_VERSION):
        dlg = ChangelogDialog(window, APP_VERSION, f"v{APP_VERSION} 업데이트 완료",
                              f"v{last} → v{APP_VERSION} 업데이트가 끝났습니다. 바뀐 점:")
        dlg.exec()
    if last != APP_VERSION:
        ws.settings.set("last_run_version", APP_VERSION)


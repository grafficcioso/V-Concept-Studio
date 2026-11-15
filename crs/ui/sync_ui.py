"""구글 드라이브 동기화 UI: 백그라운드 동기화 관리자 + 설정 탭."""
from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from .. import keystore
from .workers import FuncWorker

log = logging.getLogger(__name__)
SECRET_NAME = "google_client_secret"
REFRESH_NAME = "google_refresh_token"


def make_auth(ws):
    from ..sync.drive import GoogleAuth
    return GoogleAuth(ws.settings.get("google_client_id") or "", keystore.get_secret(SECRET_NAME) or "",
                      lambda: keystore.get_secret(REFRESH_NAME), lambda v: keystore.set_secret(REFRESH_NAME, v))


class SyncManager(QObject):
    """동기화는 별도 스레드 + 별도 DB 연결에서 돌린다 (화면은 기다리지 않는다)."""
    started = Signal()
    finished = Signal(object)     # SyncStats 또는 None
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, ws, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.worker = None
        self._again = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self.sync_now(silent=True))
        self.drive_factory = None   # 테스트용 교체 지점: (ws) -> DriveClient

    def connected(self) -> bool:
        return bool(keystore.get_secret(REFRESH_NAME)) or self.drive_factory is not None

    def schedule(self, delay_ms=5000):
        """생성·에셋 변경 뒤 몇 초 모아서 한 번 동기화."""
        if self.ws.settings.get("sync_auto") and self.connected():
            self._timer.start(delay_ms)

    def is_busy(self):
        return self.worker is not None

    def sync_now(self, silent=False):
        if not self.connected():
            if not silent:
                self.failed.emit("구글 드라이브가 연결되어 있지 않습니다. [설정 > 구글 드라이브]에서 연결하세요.")
            return False
        if self.worker is not None:
            self._again = True
            return False
        ws = self.ws
        factory = self.drive_factory

        def run():
            from ..db import Database
            from ..sync.drive import DriveClient
            from ..sync.engine import SyncEngine
            clone = copy.copy(ws)
            clone.db = Database(ws.paths.db_file)     # 이 스레드 전용 연결
            try:
                drive = factory(clone) if factory else DriveClient(make_auth(ws).access_token)
                return SyncEngine(clone, drive).sync(progress=lambda t: self.progress.emit(t))
            finally:
                clone.db.close()

        self.worker = FuncWorker(run, self)
        self.worker.done.connect(self._done)
        self.worker.error.connect(self._error)
        self.worker.finished.connect(self._cleanup)
        self.started.emit()
        self.worker.start()
        return True

    def _done(self, stats):
        log.info("sync ok: %s", stats.summary())
        self.finished.emit(stats)

    def _error(self, e):
        log.warning("sync failed: %s", e)
        self.failed.emit(str(e))

    def _cleanup(self):
        if self.worker is not None:
            self.worker.deleteLater()
        self.worker = None
        if self._again:
            self._again = False
            self.schedule(1000)

    def wait(self, msecs=10000):
        if self.worker is not None:
            self.worker.wait(msecs)


class SyncSettingsWidget(QWidget):
    """설정 > 구글 드라이브 탭."""

    def __init__(self, ws, manager: SyncManager | None, parent=None):
        super().__init__(parent)
        self.ws = ws
        self.manager = manager
        self._worker = None
        lay = QVBoxLayout(self)
        info = QLabel(
            "생성 결과와 에셋 라이브러리를 구글 드라이브의 'V-Concept Studio 동기화' 폴더로 공유합니다. "
            "웹앱(폰)에서도 같은 구글 계정으로 연결하면 서로 보입니다.\n"
            "앱이 만든 파일만 볼 수 있는 권한(drive.file)만 요청합니다. 드라이브 무료 용량을 사용합니다.\n"
            "구글 클라우드에서 만든 '데스크톱 앱' 클라이언트의 ID와 보안 비밀번호를 넣어 주세요. "
            "보안 비밀번호와 로그인 정보는 Windows 자격 증명 관리자에만 저장됩니다.")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        self.client_id = QLineEdit(ws.settings.get("google_client_id") or "")
        self.client_id.setPlaceholderText("xxxx.apps.googleusercontent.com")
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("저장됨 (바꾸려면 새로 입력)" if keystore.get_secret(SECRET_NAME) else "GOCSPX-…")
        form.addRow("클라이언트 ID", self.client_id)
        form.addRow("클라이언트 보안 비밀번호", self.secret)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.connect_btn = QPushButton("구글 계정 연결 (브라우저가 열립니다)")
        self.connect_btn.clicked.connect(self.connect_google)
        row.addWidget(self.connect_btn)
        self.disconnect_btn = QPushButton("연결 해제")
        self.disconnect_btn.clicked.connect(self.disconnect_google)
        row.addWidget(self.disconnect_btn)
        self.sync_btn = QPushButton("지금 동기화")
        self.sync_btn.clicked.connect(self.sync_now)
        row.addWidget(self.sync_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.auto = QCheckBox("자동 동기화 (프로그램을 켤 때와 생성·에셋 변경 후)")
        self.auto.setChecked(bool(ws.settings.get("sync_auto")))
        self.auto.toggled.connect(lambda v: ws.settings.set("sync_auto", bool(v)))
        lay.addWidget(self.auto)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        note = QLabel("· 한쪽에서 지운 항목은 드라이브 휴지통으로 옮겨지고(30일간 복구 가능) 다른 기기에서도 지워집니다.\n"
                      "· 이름·태그·즐겨찾기·분류는 나중에 고친 쪽이 남습니다.\n"
                      "· 원본(입력) 이미지는 공유하지 않습니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")
        lay.addWidget(note)
        b = QPushButton("구글 드라이브 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://drive.google.com/drive/my-drive")))
        lay.addWidget(b)
        lay.addStretch(1)
        self.refresh()

    def refresh(self, extra=""):
        connected = bool(keystore.get_secret(REFRESH_NAME))
        last = self.ws.settings.get("last_sync") or "-"
        self.status.setText(("● 연결됨" if connected else "○ 연결 안 됨") + f" · 마지막 동기화 {last.replace('T', ' ')[:19]}"
                            + (f"\n{extra}" if extra else ""))
        self.disconnect_btn.setEnabled(connected)
        self.sync_btn.setEnabled(connected and self.manager is not None)

    def _save_credentials(self) -> bool:
        cid = self.client_id.text().strip()
        if not cid.endswith(".apps.googleusercontent.com"):
            QMessageBox.warning(self, "구글 드라이브", "클라이언트 ID 형식이 아닙니다 (…apps.googleusercontent.com 으로 끝나야 합니다).")
            return False
        self.ws.settings.set("google_client_id", cid)
        if self.secret.text().strip():
            keystore.set_secret(SECRET_NAME, self.secret.text().strip())
            self.secret.clear()
            self.secret.setPlaceholderText("저장됨 (바꾸려면 새로 입력)")
        if not keystore.get_secret(SECRET_NAME):
            QMessageBox.warning(self, "구글 드라이브", "클라이언트 보안 비밀번호를 넣어 주세요.")
            return False
        return True

    def connect_google(self):
        if self._worker is not None or not self._save_credentials():
            return
        auth = make_auth(self.ws)
        self.status.setText("브라우저에서 구글 로그인을 마쳐 주세요… (5분 안에)")
        self.connect_btn.setEnabled(False)
        self._worker = FuncWorker(auth.sign_in, self)

        def ok(_):
            self.refresh("연결되었습니다. 첫 동기화를 시작합니다.")
            if self.manager:
                self.manager.sync_now()

        def fail(e):
            self.refresh(f"✖ {e}")

        self._worker.done.connect(ok)
        self._worker.error.connect(fail)
        self._worker.finished.connect(self._clear)
        self._worker.start()

    def _clear(self):
        self.connect_btn.setEnabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None

    def disconnect_google(self):
        if QMessageBox.question(self, "구글 드라이브", "연결을 해제할까요? 드라이브와 이 PC의 파일은 지워지지 않습니다.") \
                != QMessageBox.StandardButton.Yes:
            return
        make_auth(self.ws).sign_out()
        self.refresh("연결을 해제했습니다.")

    def sync_now(self):
        if self.manager:
            self.manager.sync_now()
            self.refresh("동기화를 시작했습니다. 상단의 ☁ 표시에서 진행 상황을 볼 수 있습니다.")

    def wait(self, msecs=10000):
        if self._worker is not None:
            self._worker.wait(msecs)

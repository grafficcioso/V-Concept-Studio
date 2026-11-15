"""메인 윈도우: 생성 / 라이브러리 / 조합 / 사용량 탭."""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from .. import APP_TITLE, APP_VERSION
from .compose_tab import ComposeTab
from .gen_controller import GenerationController
from .generate_tab import GenerateTab
from .library_tab import LibraryTab
from .settings_dialog import SettingsDialog
from .update_ui import UpdateManager, show_post_update
from .usage_tab import UsageTab


class MainWindow(QMainWindow):
    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(1480, 920)
        self.ctl = GenerationController(ws, self)
        self.gen = GenerateTab(ws, self.ctl)
        self.library = LibraryTab(ws)
        self.compose = ComposeTab(ws, self.ctl, lambda: self.gen.project_id, self.gen.current_options)
        self.usage = UsageTab(ws)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.gen, "생성")
        self.tabs.addTab(self.library, "에셋 라이브러리")
        self.tabs.addTab(self.compose, "조합")
        self.tabs.addTab(self.usage, "사용량 · 비용")
        self.setCentralWidget(self.tabs)
        self.tabs.currentChanged.connect(self._on_tab)

        self.gen.openSettings.connect(self.open_settings)
        self.gen.assetsChanged.connect(self.library.reload)
        self.library.addToCompose.connect(self._add_to_compose)
        self.library.sendToProject.connect(self._asset_to_project)
        self.compose.aiRequested.connect(lambda: self.tabs.setCurrentWidget(self.gen))
        self.ctl.usageChanged.connect(self._usage_changed)
        self.gen.style_ctl.usageChanged.connect(self._usage_changed)
        self.compose.style_provider = self.gen.apply_style
        self.statusBar().showMessage(f"데이터 폴더: {ws.paths.root}")
        self.updates = UpdateManager(ws, self, is_busy=lambda: self.ctl.is_busy() or self.gen.style_ctl.is_busy())
        self.updates.quitRequested.connect(self._quit_for_update)
        self._quitting_for_update = False
        from .sync_ui import SyncManager
        self.sync = SyncManager(ws, self)
        self.sync.started.connect(lambda: self.gen.set_sync_status("☁ 동기화 중…", busy=True))
        self.sync.progress.connect(lambda t: self.gen.set_sync_status("☁ " + t, busy=True))
        self.sync.finished.connect(self._sync_done)
        self.sync.failed.connect(self._sync_failed)
        self.gen.syncRequested.connect(self._sync_clicked)
        self.ctl.finished.connect(lambda *_: self.sync.schedule())
        self.gen.assetsChanged.connect(lambda: self.sync.schedule())
        self.gen.itemsEdited.connect(lambda: self.sync.schedule())
        self.library.assetsEdited.connect(lambda: self.sync.schedule())
        self.gen.set_sync_status("☁ 연결됨" if self.sync.connected() else "☁ 드라이브 미연결")
        from ..watchdog import GuiWatchdog
        self.watchdog = GuiWatchdog(ws.paths.logs, parent=self)

    def start_background_tasks(self, argv=None):
        """창이 뜬 뒤: 업데이트 완료 안내 → 새 버전 확인."""
        argv = sys.argv if argv is None else argv
        QTimer.singleShot(300, lambda: show_post_update(self.ws, self, argv))
        self.updates.check_on_startup()
        self.sync.schedule(6000)

    def _sync_clicked(self):
        if self.sync.connected():
            self.sync.sync_now()
        else:
            self.open_settings("google")

    def _sync_done(self, stats):
        from ..db import now_iso
        self.gen.set_sync_status(f"☁ {now_iso()[11:16]} 동기화 · {stats.summary()}", error=bool(stats.errors),
                                 tooltip="\n".join(stats.errors[:10]) or "구글 드라이브와 동기화했습니다.")
        if stats.downloaded or stats.meta_pulled or stats.deleted_local:
            self.gen.reload_projects(self.gen.project_id)
            self.gen.reload_lists()
            self.library.reload()

    def _sync_failed(self, msg):
        self.gen.set_sync_status("☁ 동기화 실패", error=True, tooltip=msg)

    def _usage_changed(self):
        self.usage.reload()
        self.gen.refresh_credit()

    def _quit_for_update(self):
        self._quitting_for_update = True
        self.close()
        QApplication.quit()

    def _on_tab(self, idx):
        w = self.tabs.widget(idx)
        if w is self.library:
            self.library.reload()
        elif w is self.usage:
            self.usage.reload()

    def open_settings(self, tab="api"):
        dlg = SettingsDialog(self.ws, self, tab)
        dlg.exec()
        if dlg.restart_required:
            QApplication.quit()
            return
        self.gen.reload_models()
        self.gen.reload_presets(self.gen.preset_combo.currentData())
        self.gen.reload_styles()
        self.gen.refresh_api_status()
        self.usage.reload()
        self.gen.set_sync_status("☁ 연결됨" if self.sync.connected() else "☁ 드라이브 미연결")
        if getattr(dlg, "update_requested", None):
            self.updates.offer(dlg.update_requested)

    def _add_to_compose(self, asset_id):
        if asset_id and self.compose.add_asset(asset_id):
            self.tabs.setCurrentWidget(self.compose)

    def _asset_to_project(self, asset_id):
        a = self.ws.db.get_asset(asset_id)
        if not a:
            return
        if not self.gen.project_id:
            QMessageBox.information(self, "프로젝트", "먼저 생성 화면에서 프로젝트를 만들어 주세요.")
            return
        try:
            iid = self.ws.import_original(self.gen.project_id, self.ws.paths.abs(a["rel_path"]), name=a["name"])
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "가져오기", str(e))
            return
        self.gen.reload_lists()
        self.gen.set_input(iid)
        self.tabs.setCurrentWidget(self.gen)

    def closeEvent(self, e):
        self.watchdog.stop()
        if self._quitting_for_update:
            self.ws.close()
            e.accept()
            return
        if self.ctl.is_busy():
            ans = QMessageBox.question(self, "종료", "생성 요청이 진행 중입니다. 취소하고 종료할까요?\n"
                                       "(서버에서 이미 처리가 시작됐다면 과금될 수 있고, 결과는 저장되지 않습니다.)")
            if ans != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.ctl.cancel()
            self.ctl.wait(8000)
        if self.gen.style_ctl.is_busy():
            self.gen.style_ctl.wait(8000)
        if self.sync.is_busy():
            self.sync.wait(15000)
        self.ws.close()
        e.accept()

"""앱 설정과 폴더 구조. 비밀 정보(API 키)는 여기에 저장하지 않는다."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME, LEGACY_APP_NAME

DEFAULTS = {
    "data_root": "",
    "monthly_budget_usd": 0.0,       # 0 = 예산 경고 사용 안 함
    "confirm_before_request": True,  # 요청 전 비용 확인 대화상자
    "timeout_sec": 300,
    "demo_mode": False,              # API 호출 없이 흐름만 확인 (결과에 DEMO 표시)
    "last_project_id": None,
    "last_model": "",
    "last_quality": "auto",
    "last_size": "auto",
    "last_background": "auto",
    "credit_total_usd": 0.0,         # 사용자가 직접 입력한 충전액 (0 = 미설정)
    "credit_since": "",              # 충전 기준일 (YYYY-MM-DD). 이 날 이후의 앱 기록 사용액을 뺀다
    "check_updates": True,
    "skipped_version": "",
    "last_run_version": "",
    "style_model": "",               # 화풍 추출용 텍스트 모델 (빈 값 = 요금표의 첫 텍스트 모델)
    "last_style_id": None,
}


def resource_dir() -> Path:
    """번들된 기본 리소스(defaults/) 위치."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _config_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def config_dir() -> Path:
    override = os.environ.get("CRS_CONFIG_DIR")
    p = Path(override) if override else _config_base() / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def legacy_config_dir() -> Path:
    override = os.environ.get("CRS_LEGACY_CONFIG_DIR")
    if override:
        return Path(override)
    if os.environ.get("CRS_CONFIG_DIR"):
        return Path(os.environ["CRS_CONFIG_DIR"]) / "__no_legacy__"
    return _config_base() / LEGACY_APP_NAME


def _docs_dir() -> Path:
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()


def default_data_root() -> Path:
    return _docs_dir() / APP_NAME


def legacy_default_data_root() -> Path:
    return _docs_dir() / LEGACY_APP_NAME


def migrate_legacy_settings(new_settings: Path) -> dict | None:
    """v0.1~0.2(CharacterReferenceStudio) 설정을 새 이름으로 이전한다.

    - 새 설정 파일이 없고 옛 설정 파일이 있을 때만 동작한다.
    - 데이터 폴더가 옛 기본 위치(문서\\CharacterReferenceStudio)면 새 기본 위치로 이름을 바꾼다.
      실패하면(다른 프로그램이 파일을 잡고 있는 등) 옛 위치를 그대로 쓴다. 데이터는 지우지 않는다.
    """
    if new_settings.exists():
        return None
    old = legacy_config_dir() / "settings.json"
    if not old.exists():
        return None
    try:
        data = json.loads(old.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
    except (OSError, ValueError):
        return None
    root = Path(data.get("data_root") or legacy_default_data_root())
    try:
        same_default = root.resolve() == legacy_default_data_root().resolve()
    except OSError:
        same_default = False
    new_root = default_data_root()
    if same_default and root.exists() and not new_root.exists():
        try:
            root.rename(new_root)
            root = new_root
        except OSError:
            pass
    data["data_root"] = str(root)
    data["last_run_version"] = data.get("last_run_version") or "0.2.0"
    data["_migrated_from"] = str(old.parent)
    return data


class Settings:
    """settings.json (사용자 프로필 폴더). API 키는 절대 포함하지 않는다."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "settings.json")
        self.data = dict(DEFAULTS)
        self.migrated_from = None
        migrated = migrate_legacy_settings(self.path)
        if migrated is not None:
            self.migrated_from = migrated.pop("_migrated_from", None)
            self.data.update({k: v for k, v in migrated.items() if k in DEFAULTS})
            self.save()
        elif self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass
        if not self.data["data_root"]:
            self.data["data_root"] = str(default_data_root())
        self._retry_legacy_rename()

    def _retry_legacy_rename(self):
        """옛 기본 폴더(문서\\CharacterReferenceStudio)를 쓰고 있으면 새 이름으로 다시 옮겨 본다. 실패하면 그대로 쓴다."""
        try:
            root = Path(self.data["data_root"])
            if root.resolve() != legacy_default_data_root().resolve() or not root.exists():
                return
            new_root = default_data_root()
            if new_root.exists():
                return
            root.rename(new_root)
        except OSError:
            return
        self.data["data_root"] = str(new_root)
        self.save()

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value, save=True):
        if key not in DEFAULTS:
            raise KeyError(key)
        self.data[key] = value
        if save:
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class AppPaths:
    """데이터 루트 아래의 폴더 구조. DB에는 루트 기준 상대 경로만 저장한다."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @property
    def projects(self): return self.root / "projects"
    @property
    def library_assets(self): return self.root / "library" / "assets"
    @property
    def library_thumbs(self): return self.root / "library" / "thumbnails"
    @property
    def prompts(self): return self.root / "prompts"
    @property
    def data(self): return self.root / "data"
    @property
    def db_file(self): return self.data / "app.db"
    @property
    def logs(self): return self.root / "logs"
    @property
    def models_file(self): return self.root / "config" / "models.json"

    def ensure(self):
        for d in (self.projects, self.library_assets, self.library_thumbs, self.prompts,
                  self.data, self.logs, self.models_file.parent):
            d.mkdir(parents=True, exist_ok=True)
        defaults = resource_dir() / "defaults"
        for src in sorted((defaults / "prompts").glob("*.md")):
            dst = self.prompts / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)
        if not self.models_file.exists():
            shutil.copyfile(defaults / "models.json", self.models_file)

    def rel(self, path: str | Path) -> str:
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    def abs(self, rel: str | None) -> Path | None:
        return (self.root / rel) if rel else None


def default_prompt_text(function_key: str) -> str:
    f = resource_dir() / "defaults" / "prompts" / f"{function_key}.md"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""

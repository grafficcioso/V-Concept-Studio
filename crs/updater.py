"""자동 업데이트: GitHub 릴리스 확인 → 다운로드 → 해시 검증 → 교체 스크립트 실행.

실행 중인 EXE 는 자기 파일을 덮어쓸 수 없으므로, 앱이 종료된 뒤 PowerShell 스크립트가
새 파일을 프로그램 폴더에 복사하고 앱을 다시 실행한다. 사용자 데이터(문서 폴더)와 API 키는 건드리지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_VERSION, EXE_NAME, GITHUB_REPO

log = logging.getLogger(__name__)
API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_ZIP = f"{EXE_NAME}-windows.zip"


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    html_url: str
    zip_url: str | None = None
    zip_size: int = 0
    sha256_url: str | None = None
    assets: list = field(default_factory=list)


def parse_version(text: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return (0,)
    return tuple(int(x or 0) for x in m.groups())


def is_newer(remote: str, local: str = APP_VERSION) -> bool:
    return parse_version(remote) > parse_version(local)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _open(url: str, timeout: float, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"{EXE_NAME}/{APP_VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def fetch_latest(timeout: float = 8.0) -> ReleaseInfo | None:
    """최신 릴리스 정보. 릴리스가 아직 없으면 None."""
    try:
        with _open(API_URL, timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"GitHub 응답 오류 ({e.code})") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise RuntimeError("인터넷 연결 또는 GitHub 접속을 확인하세요") from e
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name") or ""
    info = ReleaseInfo(version=".".join(map(str, parse_version(tag))), tag=tag, notes=data.get("body") or "",
                       html_url=data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
                       assets=[a.get("name") for a in data.get("assets", [])])
    for a in data.get("assets", []):
        if a.get("name") == ASSET_ZIP:
            info.zip_url, info.zip_size = a.get("browser_download_url"), int(a.get("size") or 0)
        elif a.get("name") == ASSET_ZIP + ".sha256":
            info.sha256_url = a.get("browser_download_url")
    return info


def check_latest(skipped: str = "", timeout: float = 8.0) -> ReleaseInfo | None:
    """현재보다 새 버전이 있으면 ReleaseInfo, 없거나 건너뛴 버전이면 None."""
    info = fetch_latest(timeout)
    if info is None or not is_newer(info.version):
        return None
    if skipped and parse_version(skipped) == parse_version(info.version):
        return None
    return info


def download(url: str, dest: Path, progress=None, cancelled=None, timeout: float = 30.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _open(url, timeout, accept="application/octet-stream") as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if cancelled and cancelled():
                raise RuntimeError("다운로드를 취소했습니다")
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dest


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(zip_path: Path, sha_text: str) -> bool:
    expected = (sha_text or "").strip().split()[0].lower() if sha_text and sha_text.strip() else ""
    return bool(expected) and sha256_of(zip_path) == expected


def install_dir() -> Path | None:
    """빌드된 EXE 로 실행 중이면 프로그램 폴더, 소스 실행이면 None."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def is_writable(folder: Path) -> bool:
    try:
        probe = folder / ".update_write_test"
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False


def extract(zip_path: Path, work: Path) -> Path:
    """압축을 풀고 EXE 가 들어 있는 폴더를 돌려준다."""
    out = work / "extracted"
    if out.exists():
        shutil.rmtree(out)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():  # 경로 탈출 방지
            target = (out / name).resolve()
            if not str(target).startswith(str(out.resolve())):
                raise RuntimeError("잘못된 업데이트 파일입니다")
        z.extractall(out)
    exe = next(out.rglob(f"{EXE_NAME}.exe"), None)
    if exe is None:
        raise RuntimeError("업데이트 파일 안에서 실행 파일을 찾지 못했습니다")
    return exe.parent


def _ps_quote(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def write_update_script(src: Path, dst: Path, pid: int, work: Path) -> Path:
    exe = dst / f"{EXE_NAME}.exe"
    log_file = work / "update.log"
    script = f"""$ErrorActionPreference = 'Continue'
$src = {_ps_quote(src)}
$dst = {_ps_quote(dst)}
$exe = {_ps_quote(exe)}
$log = {_ps_quote(log_file)}
"start $(Get-Date)" | Out-File -FilePath $log -Encoding utf8
try {{ Wait-Process -Id {int(pid)} -Timeout 90 -ErrorAction SilentlyContinue }} catch {{}}
# Claude Code 연결(MCP 서버)이 같은 폴더의 파일을 잡고 있으면 덮어쓸 수 없으므로 끝낸다 (Claude Code 에서 /mcp 로 다시 연결)
try {{ Get-Process -Name "{EXE_NAME}-mcp" -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like "$dst*" }} | Stop-Process -Force -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Milliseconds 800
$ok = $false
for ($i = 0; $i -lt 15 -and -not $ok; $i++) {{
  robocopy $src $dst /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -lt 8) {{ $ok = $true }} else {{ "robocopy code $LASTEXITCODE (try $i)" | Out-File -FilePath $log -Append -Encoding utf8; Start-Sleep -Seconds 2 }}
}}
if ($ok) {{
  "ok" | Out-File -FilePath $log -Append -Encoding utf8
  Start-Process -FilePath $exe -ArgumentList '--updated'
}} else {{
  "failed" | Out-File -FilePath $log -Append -Encoding utf8
  Start-Process -FilePath $exe -ArgumentList '--update-failed'
}}
"""
    path = work / "apply_update.ps1"
    path.write_text(script, encoding="utf-8-sig")  # BOM: Windows PowerShell 5.1 이 한글 경로를 올바로 읽도록
    return path


def launch_update_script(script: Path):
    """교체 스크립트를 숨은 창으로 실행한다.

    DETACHED_PROCESS(콘솔 없음)로 띄우면 PowerShell 이 시작되지 않으므로, 숨은 콘솔(CREATE_NO_WINDOW)을 쓴다.
    앱이 곧 종료되어도 스크립트는 계속 실행된다.
    """
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-File", str(script)], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prepare_update(info: ReleaseInfo, progress=None, cancelled=None) -> Path:
    """다운로드·검증·압축 해제까지 하고 실행할 스크립트 경로를 돌려준다 (아직 실행하지 않음)."""
    if not info.zip_url:
        raise RuntimeError("이 릴리스에는 설치 파일이 없습니다. 릴리스 페이지에서 직접 받아 주세요")
    target = install_dir()
    if target is None:
        raise RuntimeError("소스 코드로 실행 중이라 자동 설치를 할 수 없습니다")
    if not is_writable(target):
        raise RuntimeError("프로그램 폴더에 쓸 권한이 없습니다. 문서·바탕화면 같은 폴더로 옮겨 실행하거나 직접 받아 주세요")
    work = Path(tempfile.gettempdir()) / f"{EXE_NAME}_update_{info.version}"
    work.mkdir(parents=True, exist_ok=True)
    zip_path = download(info.zip_url, work / ASSET_ZIP, progress, cancelled)
    if info.sha256_url:
        with _open(info.sha256_url, 15, accept="application/octet-stream") as r:
            sha_text = r.read().decode("utf-8", "replace")
        if not verify(zip_path, sha_text):
            raise RuntimeError("다운로드한 파일이 손상되었습니다(해시 불일치). 다시 시도해 주세요")
    else:
        raise RuntimeError("검증용 해시 파일이 없어 설치하지 않았습니다. 릴리스 페이지에서 직접 받아 주세요")
    src = extract(zip_path, work)
    return write_update_script(src, target, os.getpid(), work)


def changelog_text() -> str:
    from .config import resource_dir
    f = resource_dir() / "defaults" / "CHANGELOG.md"
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return ""


def changelog_section(version: str, text: str | None = None) -> str:
    """CHANGELOG.md 에서 '## vX.Y.Z' 제목 아래 내용만 꺼낸다."""
    text = changelog_text() if text is None else text
    want = parse_version(version)
    out, taking = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if taking:
                break
            taking = parse_version(line) == want
            continue
        if taking:
            out.append(line)
    return "\n".join(out).strip()

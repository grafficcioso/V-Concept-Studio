"""Windows 진단: 자동 업데이트 교체 스크립트를 실제로 실행해 본다 (한글·공백 경로)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crs import updater  # noqa: E402

base = Path(tempfile.gettempdir()) / "업데이트 테스트"
shutil.rmtree(base, ignore_errors=True)
install = base / "내 프로그램" / "VConceptStudio"
new = base / "work" / "extracted" / "VConceptStudio"
shutil.copytree("dist/VConceptStudio", install)
shutil.copytree("dist/VConceptStudio", new)
(new / "NEW_VERSION_MARKER.txt").write_text("new")
dummy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])  # '종료를 기다릴' 가짜 앱
script = updater.write_update_script(new, install, dummy.pid, base / "work")
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", CRS_CONFIG_DIR=str(base / "cfg"), CRS_SMOKE_QUIT_MS="8000")
os.environ.update(env)
dummy.wait()
probe = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                        "Write-Output ('ps ok ' + $PSVersionTable.PSVersion)"], capture_output=True, text=True)
print("powershell probe:", probe.returncode, probe.stdout.strip(), probe.stderr.strip()[:500], flush=True)
sync = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                      capture_output=True, text=True, timeout=120)
print("sync run rc:", sync.returncode, "\nstdout:", sync.stdout[:1500], "\nstderr:", sync.stderr[:3000], flush=True)
time.sleep(25)
log = (base / "work" / "update.log")
print("update.log:", log.read_text(encoding="utf-8-sig") if log.exists() else "(none)")
print("marker copied:", (install / "NEW_VERSION_MARKER.txt").exists())
out = subprocess.run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_Process -Filter \"Name='VConceptStudio.exe'\" | Select-Object -ExpandProperty CommandLine"],
                     capture_output=True, text=True)
print("running exe:", out.stdout.strip() or "(none — may already have quit via smoke timer)")
cfg = base / "cfg" / "settings.json"
print("relaunched app wrote settings:", cfg.exists())
ok = (install / "NEW_VERSION_MARKER.txt").exists() and log.exists() and "ok" in log.read_text(encoding="utf-8-sig") and cfg.exists()
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)

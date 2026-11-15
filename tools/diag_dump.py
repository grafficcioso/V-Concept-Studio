"""Windows 진단: 멈춤 감시가 덤프 파일을 만들고, 그 덤프에서 메인 스레드 위치가 보이는지 확인."""
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from crs.watchdog import GuiWatchdog  # noqa: E402

d = Path(tempfile.gettempdir()) / "dumptest"
d.mkdir(exist_ok=True)
wd = GuiWatchdog(d, stall_sec=1.0)
app.processEvents()
time.sleep(5)  # 메인 스레드를 막는다 (네이티브 Sleep)
for _ in range(20):
    app.processEvents()
    time.sleep(0.05)
wd.stop()
print((d / "hang.log").read_text(encoding="utf-8"))
dumps = sorted(d.glob("*.dmp"))
print("DUMPS:", [(p.name, p.stat().st_size) for p in dumps])
print("DUMP_PATH=" + (str(dumps[0]) if dumps else ""))

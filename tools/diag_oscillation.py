"""Windows 진단: 결과 화면(ImageView)이 '맞춤 → 스크롤바 변화 → 크기 변화 → 다시 맞춤'을 반복하는지 찾는다."""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from crs.ui.image_view import ImageView  # noqa: E402

counts = {"n": 0}
orig = ImageView.resizeEvent


def counting(self, e):
    counts["n"] += 1
    orig(self, e)


ImageView.resizeEvent = counting
print("scale factor", os.environ.get("QT_SCALE_FACTOR"), "dpr", app.primaryScreen().devicePixelRatio(), flush=True)
found = []
for iw, ih in [(1024, 1024), (1536, 1024), (1024, 1536), (1672, 941), (2048, 1152)]:
    pm = QPixmap(iw, ih)
    pm.fill(QColor("pink"))
    pm.save("osc.png")
    v = ImageView()
    v.set_image("osc.png")
    v.show()
    app.processEvents()
    for w in range(300, 1000, 13):
        for h in range(300, 900, 17):
            counts["n"] = 0
            v.resize(w, h)
            t0 = time.time()
            while time.time() - t0 < 0.05:
                app.processEvents()
            if counts["n"] > 3:
                found.append((iw, ih, w, h, counts["n"]))
    v.close()
print("OSCILLATING:", len(found), found[:10], flush=True)
# 큰 이미지 그리기 시간
pm = QPixmap(3840, 2160)
pm.fill(QColor("pink"))
pm.save("big.png")
v = ImageView()
v.set_image("big.png")
v.resize(1600, 1000)
v.show()
app.processEvents()
t0 = time.time()
for _ in range(20):
    v.viewport().repaint()
print("repaint 4K x20: %.2fs" % (time.time() - t0), flush=True)

"""Windows 진단: UI에 쓰는 기호/이모지 글자를 그리는 데 걸리는 시간."""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

app = QApplication([])
app.setFont(QFont("Malgun Gothic", 9))
for text in ["충전액 미설정", "💳 충전액 미설정", "★ 즐겨찾기", "✎ 부분 수정…", "⚠ 주의", "✔ 연결 성공", "✖ 실패", "● API 키 등록됨", "○ API"]:
    b = QPushButton(text)
    b.resize(260, 32)
    b.show()
    app.processEvents()
    t0 = time.time()
    for _ in range(10):
        b.grab()
    dt = time.time() - t0
    print(f"{dt*100:8.1f} ms/paint  {text}", flush=True)
    b.close()

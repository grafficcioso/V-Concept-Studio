"""CHANGELOG.md 에서 해당 버전 부분만 뽑아 릴리스 설명으로 출력한다.  사용: python tools/release_notes.py v0.3.0"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crs.updater import changelog_section  # noqa: E402

tag = sys.argv[1] if len(sys.argv) > 1 else ""
text = changelog_section(tag) or f"{tag} 릴리스"
sys.stdout.reconfigure(encoding="utf-8")
print(text)
print("\n---\n설치: 아래 VConceptStudio-windows.zip 을 받아 압축을 풀고 VConceptStudio.exe 를 실행하세요. "
      "이미 설치되어 있다면 프로그램이 실행될 때 업데이트를 안내합니다.")

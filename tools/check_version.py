"""태그와 앱 버전이 같은지 확인한다 (릴리스 실수 방지).  사용: python tools/check_version.py v0.3.0"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from crs import APP_VERSION  # noqa: E402
from crs.updater import parse_version  # noqa: E402

tag = sys.argv[1]
if parse_version(tag) != parse_version(APP_VERSION):
    sys.exit(f"태그 {tag} 와 앱 버전 {APP_VERSION} 이 다릅니다. crs/__init__.py 의 APP_VERSION 을 맞추세요.")
print(f"ok: {tag} == {APP_VERSION}")

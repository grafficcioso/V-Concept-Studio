"""웹앱 배포 폴더(_site) 만들기: web/ + defaults 의 프롬프트·요금표를 data/ 로 복사."""
import shutil
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "_site"
if out.exists():
    shutil.rmtree(out)
shutil.copytree(root / "web", out)
(out / "data" / "prompts").mkdir(parents=True)
for f in (root / "defaults" / "prompts").glob("*.md"):
    shutil.copyfile(f, out / "data" / "prompts" / f.name)
shutil.copyfile(root / "defaults" / "models.json", out / "data" / "models.json")
shutil.move(str(out / "WEB_CHANGELOG.md"), out / "data" / "WEB_CHANGELOG.md")
shutil.move(str(out / "config.json"), out / "data" / "config.json")
sw = out / "sw.js"
sw.write_text(sw.read_text(encoding="utf-8").replace("__BUILD__", time.strftime("%Y%m%d%H%M%S")), encoding="utf-8")
(out / ".nojekyll").write_text("")
print("built", out)

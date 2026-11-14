"""dist/VConceptStudio 폴더를 업데이트용 zip 과 sha256 파일로 묶는다."""
import hashlib
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
name = "VConceptStudio-windows"
zip_path = Path(shutil.make_archive(str(root / name), "zip", root / "dist", "VConceptStudio"))
h = hashlib.sha256(zip_path.read_bytes()).hexdigest()
(root / f"{name}.zip.sha256").write_text(f"{h}  {zip_path.name}\n", encoding="ascii")
print(zip_path, h, zip_path.stat().st_size)

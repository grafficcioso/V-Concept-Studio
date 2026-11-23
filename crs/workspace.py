"""앱 코어: 설정 + 폴더 + DB + 요금표 + 파일 작업을 묶은 객체. UI와 무관하게 테스트 가능."""
from __future__ import annotations

import io
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import keystore
from .config import AppPaths, Settings, default_prompt_text
from .db import Database, now_iso
from .functions import CATEGORIES
from .logging_setup import setup_logging
from .pricing import Catalog
from .services.image_service import DemoImageService, ImageGenerationService
from .services.style_service import DemoStyleService, StyleExtractionService

log = logging.getLogger(__name__)
SUPPORTED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
THUMB = 256


def safe_name(text: str, fallback="item") -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (text or "").strip()).strip(". ")
    return s[:60] or fallback


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    p = folder / f"{stem}{ext}"
    i = 2
    while p.exists():
        p = folder / f"{stem}_{i}{ext}"
        i += 1
    return p


class Workspace:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.paths = AppPaths(self.settings.get("data_root"))
        self.paths.ensure()
        setup_logging(self.paths.logs)
        self.db = Database(self.paths.db_file)
        self.catalog = Catalog(self.paths.models_file)
        self._real_service = ImageGenerationService(keystore.get_key)
        self._demo_service = DemoImageService()
        self._real_style_service = StyleExtractionService(keystore.get_key)
        self._demo_style_service = DemoStyleService()
        log.info("workspace opened: %s", self.paths.root)

    def close(self):
        self.db.close()

    @property
    def demo_mode(self) -> bool:
        return bool(self.settings.get("demo_mode"))

    @property
    def service(self):
        return self._demo_service if self.demo_mode else self._real_service

    @property
    def style_service(self):
        return self._demo_style_service if self.demo_mode else self._real_style_service

    def style_model(self) -> str:
        chosen = self.settings.get("style_model") or ""
        ids = self.catalog.text_model_ids()
        return chosen or (ids[0] if ids else "")

    # ---- 프롬프트 --------------------------------------------------------
    def prompt_file(self, function_key: str) -> Path:
        return self.paths.prompts / f"{function_key}.md"

    def default_prompt(self, function_key: str) -> str:
        """기능의 '기본' 프롬프트 (prompts/<key>.md, 사용자 편집 가능)."""
        f = self.prompt_file(function_key)
        try:
            text = f.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        return text or default_prompt_text(function_key)

    def save_default_prompt(self, function_key: str, text: str):
        self.prompt_file(function_key).write_text(text.strip() + "\n", encoding="utf-8")

    def reset_default_prompt(self, function_key: str) -> str:
        text = default_prompt_text(function_key)
        self.save_default_prompt(function_key, text)
        return text

    def resolve_prompt(self, function_key: str, preset_id: int | None) -> tuple[str, str]:
        """(프롬프트, 프리셋 이름)"""
        preset = self.db.get_preset(preset_id)
        if preset:
            return preset["prompt"], preset["name"]
        return self.default_prompt(function_key), "기본"

    # ---- 프로젝트 --------------------------------------------------------
    def create_project(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("프로젝트 이름을 입력하세요.")
        if any(p["name"] == name for p in self.db.list_projects()):
            raise ValueError("같은 이름의 프로젝트가 이미 있습니다.")
        folder = unique_path(self.paths.projects, safe_name(name, "project"), "")
        for sub in ("originals", "outputs", "assets", "thumbs", "compositions"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        pid = self.db.create_project(name, self.paths.rel(folder))
        self.write_project_json(pid)
        return pid

    def project_dir(self, project_id) -> Path:
        return self.paths.abs(self.db.get_project(project_id)["folder"])

    def write_project_json(self, project_id):
        """프로젝트 폴더만 봐도 내용을 알 수 있도록 메타데이터 사본을 남긴다 (비밀 정보 없음)."""
        proj = self.db.get_project(project_id)
        if not proj:
            return
        keep = ("id", "kind", "rel_path", "name", "tags", "note", "favorite", "created_at", "source_image_id",
                "function", "preset_name", "prompt", "model", "quality", "size", "background", "request_id",
                "est_cost_usd")
        images = []
        for im in reversed(self.db.list_images(project_id)):
            row = {k: im[k] for k in keep}
            row["usage"] = json.loads(im["usage_json"]) if im["usage_json"] else None
            images.append(row)
        data = {"app": "V-Concept Studio", "project": {"id": proj["id"], "name": proj["name"],
                "created_at": proj["created_at"]}, "updated_at": now_iso(), "images": images}
        try:
            (self.project_dir(project_id) / "project.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("project.json 저장 실패: %s", e)

    # ---- 이미지 ----------------------------------------------------------
    def _thumb(self, src: Path, dst: Path):
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((THUMB, THUMB))
                im.convert("RGBA").save(dst, "PNG")
        except Exception as e:
            log.warning("썸네일 생성 실패 %s: %s", src.name, e)

    def import_original(self, project_id, source, name: str = "") -> int:
        """원본 가져오기. source: 파일 경로 또는 PIL.Image. 프로젝트 폴더에 사본을 만든다(원본 미변경)."""
        folder = self.project_dir(project_id) / "originals"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if isinstance(source, Image.Image):
            dst = unique_path(folder, f"paste_{stamp}", ".png")
            source.save(dst, "PNG")
            name = name or f"붙여넣기 {stamp[9:11]}:{stamp[11:13]}"
        else:
            src = Path(source)
            if src.suffix.lower() not in SUPPORTED_EXT:
                raise ValueError(f"지원하지 않는 형식입니다: {src.suffix or src.name} (PNG/JPG/WEBP만 가능)")
            try:
                with Image.open(src) as im:
                    im.verify()
            except Exception as e:
                raise ValueError(f"이미지 파일을 열 수 없습니다: {src.name}") from e
            dst = unique_path(folder, safe_name(src.stem, "original"), src.suffix.lower())
            shutil.copyfile(src, dst)
            name = name or src.stem
        thumb = self.project_dir(project_id) / "thumbs" / (dst.stem + "_o.png")
        self._thumb(dst, thumb)
        iid = self.db.add_image(project_id=project_id, kind="original", rel_path=self.paths.rel(dst),
                                thumb_path=self.paths.rel(thumb), name=name)
        self.write_project_json(project_id)
        return iid

    def import_pin(self, project_id, pin, image_bytes: bytes, source_url: str, board: str) -> int:
        """v0.6.0: 핀터레스트 핀을 원본으로 가져온다. '외부 출처' 태그 + 출처 메모, 다시 가져오지 않게 기록."""
        from .pinterest import EXTERNAL_NOTE, EXTERNAL_TAG
        import io
        with Image.open(io.BytesIO(image_bytes)) as im:
            im.verify()
            fmt = (im.format or "").upper()
        ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(fmt)
        if not ext:
            raise ValueError(f"지원하지 않는 이미지 형식입니다: {fmt or '알 수 없음'}")
        folder = self.project_dir(project_id) / "originals"
        dst = unique_path(folder, f"pin_{safe_name(pin.pin_id, 'pin')[:40]}", ext)
        dst.write_bytes(image_bytes)
        thumb = self.project_dir(project_id) / "thumbs" / (dst.stem + "_o.png")
        self._thumb(dst, thumb)
        name = (pin.title or f"핀 {pin.pin_id}")[:60]
        iid = self.db.add_image(project_id=project_id, kind="original", rel_path=self.paths.rel(dst),
                                thumb_path=self.paths.rel(thumb), name=name, tags=f"{EXTERNAL_TAG}, pinterest",
                                note=EXTERNAL_NOTE.format(link=pin.link))
        self.db.add_pinterest_pin(pin.pin_id, board, iid, source_url)
        self.write_project_json(project_id)
        return iid

    def save_output(self, project_id, image_bytes: bytes, *, function, source_image_id, prompt, preset_id,
                    preset_name, model, quality, size, background, params: dict, request_id, usage,
                    est_cost_usd) -> int:
        folder = self.project_dir(project_id) / "outputs"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = unique_path(folder, f"{function}_{stamp}", ".png")
        with Image.open(io.BytesIO(image_bytes)) as im:  # 형식과 무관하게 PNG로 저장
            im.load()
            im.save(dst, "PNG")
        thumb = self.project_dir(project_id) / "thumbs" / (dst.stem + ".png")
        self._thumb(dst, thumb)
        iid = self.db.add_image(
            project_id=project_id, kind="output", rel_path=self.paths.rel(dst), thumb_path=self.paths.rel(thumb),
            source_image_id=source_image_id, function=function, preset_id=preset_id, preset_name=preset_name,
            prompt=prompt, model=model, quality=quality, size=size, background=background,
            params_json=json.dumps(params, ensure_ascii=False), request_id=request_id,
            usage_json=json.dumps(usage) if usage else None, est_cost_usd=est_cost_usd)
        self.write_project_json(project_id)
        return iid

    def save_part_input(self, project_id, image: Image.Image, source_image_id, name: str) -> int:
        """파트 사면도용으로 잘라낸 입력 이미지를 저장한다 (원본 목록·생성 기록에는 나오지 않는다)."""
        folder = self.project_dir(project_id) / "part_inputs"
        folder.mkdir(exist_ok=True)
        dst = unique_path(folder, f"part_{datetime.now().strftime('%Y%m%d_%H%M%S')}", ".png")
        image.save(dst, "PNG")
        thumb = self.project_dir(project_id) / "thumbs" / (dst.stem + "_p.png")
        self._thumb(dst, thumb)
        iid = self.db.add_image(project_id=project_id, kind="part_input", rel_path=self.paths.rel(dst),
                                thumb_path=self.paths.rel(thumb), name=name, source_image_id=source_image_id)
        self.write_project_json(project_id)
        return iid

    def credit_status(self) -> dict | None:
        """사용자가 입력한 충전액 기준의 추정 잔액. 충전액이 없으면 None."""
        total = float(self.settings.get("credit_total_usd") or 0)
        if total <= 0:
            return None
        since = self.settings.get("credit_since") or ""
        summary = self.db.usage_summary(since + "T00:00:00" if since else "0000")
        used = summary["cost"]
        return {"total": total, "since": since, "used": used, "remaining": total - used,
                "unknown": summary["unknown"], "uncosted": summary["ok_uncosted"]}

    def image_path(self, image_row) -> Path | None:
        return self.paths.abs(image_row["rel_path"]) if image_row else None

    def root_original_id(self, image_id) -> int | None:
        """결과 이미지의 계보를 따라 올라가 최초 원본 ID를 찾는다."""
        seen = set()
        row = self.db.get_image(image_id)
        while row and row["id"] not in seen:
            seen.add(row["id"])
            if row["kind"] == "original":
                return row["id"]
            row = self.db.get_image(row["source_image_id"])
        return None

    def delete_image(self, image_id, tombstone=True):
        row = self.db.get_image(image_id)
        if not row:
            return
        for rel in (row["rel_path"], row["thumb_path"]):
            p = self.paths.abs(rel)
            if p and p.exists():
                try:
                    p.unlink()
                except OSError as e:
                    log.warning("파일 삭제 실패: %s", e)
        self.db.delete_image(image_id, tombstone=tombstone)
        self.write_project_json(row["project_id"])

    # ---- 에셋 라이브러리 -------------------------------------------------
    def register_asset(self, source, category: str, name: str = "", tags: str = "", note: str = "",
                       source_image_id=None) -> int:
        """에셋 등록. 라이브러리 내부에 PNG 사본을 만들어 원본이 사라져도 유지된다."""
        if category not in CATEGORIES:
            raise ValueError(f"알 수 없는 분류: {category}")
        if isinstance(source, Image.Image):
            im = source.copy()
        else:
            with Image.open(source) as opened:
                im = ImageOps.exif_transpose(opened).copy()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = unique_path(self.paths.library_assets, f"{category.lower()}_{safe_name(name, 'asset')}_{stamp}", ".png")
        im.save(dst, "PNG")
        thumb = self.paths.library_thumbs / dst.name
        self._thumb(dst, thumb)
        src_row = self.db.get_image(source_image_id)
        params = None
        if src_row:
            params = {k: src_row[k] for k in ("function", "model", "quality", "size", "background", "preset_name",
                                              "request_id")}
        return self.db.add_asset(
            rel_path=self.paths.rel(dst), thumb_path=self.paths.rel(thumb), category=category,
            name=name or dst.stem, tags=tags, note=note, source_image_id=source_image_id,
            source_original_id=self.root_original_id(source_image_id) if source_image_id else None,
            preset_id=src_row["preset_id"] if src_row else None,
            params_json=json.dumps(params, ensure_ascii=False) if params else None)

    def delete_asset(self, asset_id, tombstone=True):
        row = self.db.get_asset(asset_id)
        if not row:
            return
        for rel in (row["rel_path"], row["thumb_path"]):
            p = self.paths.abs(rel)
            if p and p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        self.db.delete_asset(asset_id, tombstone=tombstone)

    # ---- 사용량 ----------------------------------------------------------
    def month_start(self) -> str:
        return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    def day_start(self) -> str:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    # ---- 데이터 폴더 이동 ------------------------------------------------
    def copy_data_to(self, new_root: Path):
        """현재 데이터를 새 위치로 복사한다 (기존 폴더는 그대로 둔다)."""
        new_root = Path(new_root)
        self.db.conn.commit()
        shutil.copytree(self.paths.root, new_root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("logs"))


def remove_background_simple(im: Image.Image, tolerance: int = 24) -> Image.Image:
    """가장자리와 이어진 단색 배경을 투명하게 만드는 단순 처리.

    AI 배경 제거가 아니다. 배경과 비슷한 색의 부품 가장자리가 함께 지워지거나
    배경이 남을 수 있다 (UI에서 그 사실을 안내한다).
    """
    rgba = im.convert("RGBA")
    rgb = rgba.convert("RGB")
    w, h = rgb.size
    marker = (255, 0, 254)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    bg = rgb.getpixel((0, 0))
    for s in seeds:
        px = rgb.getpixel(s)
        if px != marker and max(abs(a - b) for a, b in zip(px, bg)) <= tolerance:
            ImageDraw.floodfill(rgb, s, marker, thresh=tolerance)
    mask = Image.eval(ImageChopsDiff(rgb, marker), lambda v: 255 if v else 0)
    alpha = rgba.getchannel("A")
    rgba.putalpha(Image.composite(alpha, Image.new("L", rgba.size, 0), mask))
    return rgba


def isolate_part(image: Image.Image, painted: Image.Image, pad_ratio: float = 0.08) -> Image.Image:
    """칠한 영역만 남기고 나머지는 흰색으로 지운 뒤, 그 영역에 맞게 잘라 여백을 붙인다.

    painted: image 와 같은 크기의 RGBA (알파>0 = 칠한 곳). 칠한 곳이 없으면 ValueError.
    경계가 잘리지 않도록 칠한 영역을 조금 넓혀서(팽창) 사용한다.
    """
    if painted.size != image.size:
        raise ValueError("칠한 레이어와 이미지 크기가 다릅니다.")
    mask = painted.getchannel("A").point(lambda v: 255 if v > 0 else 0)
    grow = max(3, (min(image.size) // 150) | 1)
    mask = mask.filter(ImageFilter.MaxFilter(grow))
    box = mask.getbbox()
    if box is None:
        raise ValueError("칠한 영역이 없습니다.")
    rgba = image.convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba).convert("RGB")
    white = Image.new("RGB", rgba.size, (255, 255, 255))
    kept = Image.composite(flat, white, mask).crop(box)
    w, h = kept.size
    pad = int(max(w, h) * pad_ratio) + 8
    out = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (255, 255, 255))
    out.paste(kept, (pad, pad))
    return out


def ImageChopsDiff(rgb: Image.Image, color) -> Image.Image:
    """color 와 다른 픽셀은 0이 아닌 값이 되는 L 이미지."""
    from PIL import ImageChops
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, color))
    r, g, b = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)

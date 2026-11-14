"""화풍 프롬프트 추출: 이미지 인식이 되는 텍스트 모델(Responses API)에 그림을 보내 화풍 설명을 받는다.

이미지 생성이 아니라 텍스트 응답이므로 비용이 작지만, 유료 요청이다. 자동 재시도는 하지 않는다.
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from .image_service import GenerationError, map_openai_error

MAX_SIDE = 1024  # 화풍 분석에는 이 정도면 충분하고, 입력 토큰을 줄인다

SCHEMA = {
    "type": "object",
    "properties": {
        "prompt_en": {"type": "string"},
        "summary_ko": {"type": "string"},
    },
    "required": ["prompt_en", "summary_ko"],
    "additionalProperties": False,
}


@dataclass
class StyleResult:
    prompt_en: str
    summary_ko: str
    model: str
    usage: dict | None = None
    request_id: str | None = None
    elapsed_sec: float = 0.0
    demo: bool = False


def image_data_url(path: Path) -> str:
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im)
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            im.save(buf, "PNG")
    except Exception as e:
        raise GenerationError("input", f"이미지를 읽을 수 없습니다: {Path(path).name}", detail=repr(e)) from e
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_output(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    try:
        data = json.loads(text)
        return str(data.get("prompt_en", "")).strip(), str(data.get("summary_ko", "")).strip()
    except (ValueError, AttributeError):
        return text, ""


class StyleExtractionService:
    def __init__(self, api_key_provider, http_client=None, base_url=None):
        self._key_provider = api_key_provider
        self._http_client = http_client
        self._base_url = base_url
        self._active = None
        self._cancelled = threading.Event()

    def reset_cancel(self):
        self._cancelled.clear()

    def cancel(self):
        self._cancelled.set()
        if self._active is not None:
            try:
                self._active.close()
            except Exception:
                pass

    def extract(self, image_path: Path, model: str, instructions: str, reasoning_effort: str | None = None,
                timeout_sec: float = 180) -> StyleResult:
        import openai
        key = self._key_provider()
        if not key:
            raise GenerationError("auth", "OpenAI API 키가 설정되지 않았습니다. [설정 > API]에서 키를 등록하세요.")
        if not model:
            raise GenerationError("input", "화풍 추출 모델이 지정되지 않았습니다. [설정 > 모델·요금표]를 확인하세요.")
        url = image_data_url(image_path)
        kwargs = dict(api_key=key, max_retries=0, timeout=timeout_sec)
        if self._http_client is not None:
            kwargs["http_client"] = self._http_client
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = openai.OpenAI(**kwargs)
        if self._cancelled.is_set():
            raise GenerationError("cancelled", "요청을 보내기 전에 취소했습니다. (과금 없음)")
        self._active = client
        started = time.monotonic()
        params = dict(
            model=model,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": instructions},
                {"type": "input_image", "image_url": url, "detail": "high"},
            ]}],
            text={"format": {"type": "json_schema", "name": "art_style", "schema": SCHEMA, "strict": True}},
            max_output_tokens=6000,
        )
        if reasoning_effort:
            params["reasoning"] = {"effort": reasoning_effort}
        try:
            raw = client.responses.with_raw_response.create(**params)
            request_id = raw.headers.get("x-request-id")
            resp = raw.parse()
        except Exception as e:
            if self._cancelled.is_set():
                raise GenerationError("cancelled", "요청을 취소했습니다. 서버에서 이미 처리가 시작된 경우 과금될 수 있습니다.",
                                      billing_uncertain=True) from e
            raise map_openai_error(e) from e
        finally:
            self._active = None
            if self._http_client is None:
                try:
                    client.close()
                except Exception:
                    pass
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) is not None else None
        prompt_en, summary_ko = parse_output(getattr(resp, "output_text", "") or "")
        if not prompt_en:
            status = getattr(resp, "status", "")
            raise GenerationError("server", "모델이 화풍 설명을 돌려주지 않았습니다"
                                  + (f" (상태: {status})" if status else "") + ". 다시 시도해 보세요.",
                                  request_id=request_id)
        return StyleResult(prompt_en, summary_ko, model, usage, request_id, time.monotonic() - started)


class DemoStyleService:
    def __init__(self, delay=0.6):
        self.delay = delay
        self._cancelled = threading.Event()

    def reset_cancel(self):
        self._cancelled.clear()

    def cancel(self):
        self._cancelled.set()

    def extract(self, image_path, model, instructions, reasoning_effort=None, timeout_sec=180) -> StyleResult:
        end = time.monotonic() + self.delay
        while time.monotonic() < end:
            if self._cancelled.is_set():
                raise GenerationError("cancelled", "요청을 취소했습니다. (데모 모드 — 과금 없음)")
            time.sleep(0.05)
        return StyleResult(
            "DEMO STYLE (not a real analysis): clean anime illustration style, thin dark-gray line art with slight "
            "line weight variation, soft cel shading with two tones, gentle pastel palette with low saturation, "
            "large glossy eyes with layered highlights, hair drawn in smooth clumps with soft highlight bands.",
            "- 데모 결과입니다 (실제 분석 아님)\n- 얇은 회색 선화, 2단 셀 채색\n- 채도 낮은 파스텔 색감",
            model or "demo", None, "demo", self.delay, demo=True)

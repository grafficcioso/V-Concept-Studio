"""OpenAI 이미지 생성/편집 API 호출을 격리한 서비스 계층.

- 공식 Python SDK 의 Images API (images.edit / images.generate) 를 사용한다.
- 자동 재시도는 끈다(max_retries=0): 네트워크 오류 뒤 과금 여부를 알 수 없는 상태에서
  중복 요청이 나가지 않도록 하기 위함. 재시도는 사용자가 직접 누른다.
- UI 스레드에서 직접 호출하지 말 것 (crs.ui.gen_controller 의 QThread 워커 사용).
"""
from __future__ import annotations

import base64
import io
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

log = logging.getLogger(__name__)

MAX_INPUT_IMAGES = 16
MAX_INPUT_BYTES = 50 * 1024 * 1024
PASSTHROUGH_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


@dataclass
class GenerationRequest:
    input_images: list[Path]
    prompt: str
    model: str
    quality: str = "auto"
    size: str = "auto"
    background: str = "auto"
    input_fidelity: str | None = None
    markup: Path | None = None   # 첫 입력 이미지와 같은 크기의 투명 PNG 주석 레이어 (영역 지정 수정)
    timeout_sec: float = 300.0


@dataclass
class GenerationResult:
    image_bytes: bytes
    model: str
    request_id: str | None = None
    usage: dict | None = None
    size: str | None = None
    quality: str | None = None
    background: str | None = None
    output_format: str | None = None
    elapsed_sec: float = 0.0
    demo: bool = False
    extra: dict = field(default_factory=dict)


class GenerationError(Exception):
    """사용자에게 보여줄 한국어 메시지를 가진 오류.

    kind: auth | quota | rate_limit | network | timeout | unsupported | moderation |
          permission | not_found | server | cancelled | input | unknown
    billing_uncertain: 요청이 서버에 도달했을 수 있어 과금 여부를 확인할 수 없는 경우.
    """

    def __init__(self, kind: str, message: str, *, billing_uncertain=False, request_id=None, detail=""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.billing_uncertain = billing_uncertain
        self.request_id = request_id
        self.detail = detail


def prepare_input_image(path: Path) -> tuple[str, bytes, str]:
    """입력 이미지를 검증하고 필요하면 PNG로 변환한다. 원본 파일은 건드리지 않는다."""
    path = Path(path)
    try:
        with Image.open(path) as im:
            im.verify()  # 손상된 파일 걸러내기 (verify 는 open 직후에만 가능)
        with Image.open(path) as im:
            fmt = (im.format or "").upper()
            orientation = im.getexif().get(0x0112, 1)
        if fmt in PASSTHROUGH_FORMATS and orientation in (1, None) and path.stat().st_size < MAX_INPUT_BYTES:
            return path.name, path.read_bytes(), PASSTHROUGH_FORMATS[fmt]
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P", "PA") else "RGB")
            buf = io.BytesIO()
            im.save(buf, "PNG")
    except GenerationError:
        raise
    except Exception as e:
        raise GenerationError("input", f"입력 이미지를 읽을 수 없습니다: {path.name}", detail=repr(e)) from e
    data = buf.getvalue()
    if len(data) >= MAX_INPUT_BYTES:
        raise GenerationError("input", f"입력 이미지가 너무 큽니다(50MB 이상): {path.name}")
    return path.stem + ".png", data, "image/png"


class ImageGenerationService:
    """generate(request) -> GenerationResult. 실패 시 GenerationError."""

    def __init__(self, api_key_provider, http_client=None, base_url=None):
        self._key_provider = api_key_provider
        self._http_client = http_client     # 테스트용 주입
        self._base_url = base_url
        self._active = None
        self._cancelled = threading.Event()

    # ---- client ---------------------------------------------------------
    def _client(self, timeout: float):
        import openai
        key = self._key_provider()
        if not key:
            raise GenerationError("auth", "OpenAI API 키가 설정되지 않았습니다. [설정 > API]에서 키를 등록하세요.")
        kwargs = dict(api_key=key, max_retries=0, timeout=timeout)
        if self._http_client is not None:
            kwargs["http_client"] = self._http_client
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return openai.OpenAI(**kwargs)

    def cancel(self):
        """진행 중인 요청의 연결을 끊는다. 서버 쪽 처리가 이미 시작됐다면 과금될 수 있다."""
        self._cancelled.set()
        client = self._active
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    # ---- API ------------------------------------------------------------
    def reset_cancel(self):
        """새 요청을 시작하기 전에 호출한다 (요청 시작 직후의 취소가 유실되지 않도록 generate 안에서는 지우지 않는다)."""
        self._cancelled.clear()

    def generate(self, req: GenerationRequest) -> GenerationResult:
        if not req.prompt.strip():
            raise GenerationError("input", "프롬프트가 비어 있습니다.")
        if not req.model:
            raise GenerationError("input", "모델이 선택되지 않았습니다.")
        if len(req.input_images) > MAX_INPUT_IMAGES:
            raise GenerationError("input", f"참조 이미지는 최대 {MAX_INPUT_IMAGES}장까지 보낼 수 있습니다.")
        files = [prepare_input_image(p) for p in req.input_images]
        if req.markup and not files:
            raise GenerationError("input", "영역 지정 수정에는 입력 이미지가 필요합니다.")

        kwargs = dict(model=req.model, prompt=req.prompt, n=1)
        if req.quality and req.quality != "auto":
            kwargs["quality"] = req.quality
        if req.size and req.size != "auto":
            kwargs["size"] = req.size
        if req.background and req.background != "auto":
            kwargs["background"] = req.background
            if req.background == "transparent":
                kwargs["output_format"] = "png"

        client = self._client(req.timeout_sec)
        if self._cancelled.is_set():  # 아직 아무것도 보내지 않았으므로 과금 없음
            raise GenerationError("cancelled", "요청을 보내기 전에 취소했습니다. (과금 없음)")
        self._active = client
        started = time.monotonic()
        try:
            if files:
                if req.input_fidelity:
                    kwargs["input_fidelity"] = req.input_fidelity
                if req.markup:
                    kwargs["markup"] = (Path(req.markup).name, Path(req.markup).read_bytes(), "image/png")
                raw = client.images.with_raw_response.edit(image=files, **kwargs)
            else:
                raw = client.images.with_raw_response.generate(**kwargs)
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
        # 취소를 눌렀더라도 응답이 이미 도착했다면(과금 완료) 결과를 버리지 않고 돌려준다.
        item = resp.data[0] if resp.data else None
        image_bytes = None
        if item is not None and getattr(item, "b64_json", None):
            image_bytes = base64.b64decode(item.b64_json)
        if not image_bytes:
            raise GenerationError("server", "API 응답에 이미지 데이터가 없습니다. 과금되었을 수 있으니 사용량을 확인하세요.",
                                  billing_uncertain=True, request_id=request_id)
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) is not None else None
        return GenerationResult(
            image_bytes=image_bytes, model=req.model, request_id=request_id, usage=usage,
            size=getattr(resp, "size", None), quality=getattr(resp, "quality", None),
            background=getattr(resp, "background", None), output_format=getattr(resp, "output_format", None),
            elapsed_sec=time.monotonic() - started)

    def list_image_models(self) -> list[str]:
        """계정에서 접근 가능한 모델 중 이미지 모델 ID. (과금 없는 조회)"""
        client = self._client(30)
        try:
            ids = sorted(m.id for m in client.models.list() if "image" in m.id.lower())
        except Exception as e:
            raise map_openai_error(e) from e
        finally:
            if self._http_client is None:
                client.close()
        return ids


def map_openai_error(e: Exception) -> GenerationError:
    """SDK 예외 → 원인별 한국어 안내."""
    import openai

    if isinstance(e, GenerationError):
        return e
    body = getattr(e, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else {}
    code = (err.get("code") if isinstance(err, dict) else None) or getattr(e, "code", None) or ""
    api_msg = (err.get("message") if isinstance(err, dict) else None) or ""
    param = (err.get("param") if isinstance(err, dict) else None) or ""
    rid = getattr(e, "request_id", None)
    detail = f"{type(e).__name__} code={code} param={param} msg={api_msg}"[:600]
    low = api_msg.lower()

    def make(kind, msg, **kw):
        return GenerationError(kind, msg, request_id=rid, detail=detail, **kw)

    if isinstance(e, openai.APITimeoutError):
        return make("timeout", "응답 대기 시간이 초과되었습니다. 서버가 요청을 처리했을 수 있어 과금 여부를 확인할 수 없습니다. "
                    "자동 재시도는 하지 않습니다. 사용량을 확인한 뒤 직접 다시 시도하세요.", billing_uncertain=True)
    if isinstance(e, openai.APIConnectionError):
        return make("network", "네트워크 오류로 OpenAI 서버와 통신하지 못했습니다. 인터넷 연결을 확인하세요. "
                    "요청이 서버에 도달했는지 알 수 없어 과금 여부가 불확실합니다. 자동 재시도는 하지 않습니다.",
                    billing_uncertain=True)
    if isinstance(e, openai.AuthenticationError):
        return make("auth", "API 키가 올바르지 않거나 만료되었습니다. [설정 > API]에서 키를 다시 등록하세요.")
    if isinstance(e, openai.RateLimitError):
        if code == "insufficient_quota" or "quota" in low or "billing" in low:
            return make("quota", "OpenAI 계정의 크레딧이 부족하거나 결제 한도를 초과했습니다. "
                        "OpenAI 대시보드의 Billing에서 잔액을 확인하세요.")
        return make("rate_limit", "요청 한도(레이트 리밋)에 걸렸습니다. 잠시 후 직접 다시 시도하세요.")
    if isinstance(e, openai.PermissionDeniedError):
        if "verif" in low:
            return make("permission", "이 모델을 쓰려면 OpenAI 조직 인증(Organization Verification)이 필요합니다. "
                        "OpenAI 개발자 콘솔에서 인증을 완료하세요.")
        return make("permission", "이 API 키로는 해당 모델/기능을 사용할 권한이 없습니다. "
                    f"프로젝트 권한이나 지역 제한을 확인하세요.\n({api_msg})")
    if isinstance(e, openai.NotFoundError):
        return make("not_found", "선택한 모델을 찾을 수 없습니다. 모델이 종료되었거나 계정에서 사용할 수 없습니다. "
                    "[설정 > 모델·요금표]에서 '서버에서 모델 목록 가져오기'로 사용 가능한 모델을 확인하세요.")
    if isinstance(e, (openai.BadRequestError, openai.UnprocessableEntityError)):
        if "moderation" in str(code) or "safety" in low or "content policy" in low or "content_policy" in str(code):
            return make("moderation", "안전 정책에 의해 요청이 거부되었습니다. 입력 이미지나 프롬프트를 조정해 보세요.\n"
                        f"({api_msg})")
        if "verif" in low:
            return make("permission", "이 모델을 쓰려면 OpenAI 조직 인증(Organization Verification)이 필요합니다.")
        if "model" in str(param) or "does not exist" in low or "model_not_found" in str(code):
            return make("not_found", f"이 모델은 사용할 수 없습니다. 다른 모델을 선택하세요.\n({api_msg})")
        which = f" (문제 옵션: {param})" if param else ""
        return make("unsupported", f"선택한 모델이 지원하지 않는 옵션이거나 잘못된 요청입니다{which}. "
                    f"품질/크기/배경 옵션을 바꿔 보세요.\n({api_msg})")
    if isinstance(e, openai.APIStatusError):
        status = getattr(e, "status_code", 0)
        if status >= 500:
            return make("server", f"OpenAI 서버 오류({status})입니다. 과금 여부가 불확실할 수 있습니다. "
                        "잠시 후 직접 다시 시도하세요.", billing_uncertain=True)
        return make("unknown", f"요청이 거부되었습니다({status}).\n({api_msg})")
    return GenerationError("unknown", f"알 수 없는 오류가 발생했습니다: {type(e).__name__}", detail=repr(e)[:600],
                           billing_uncertain=True)


class DemoImageService:
    """데모 모드: 네트워크/과금 없이 전체 흐름을 확인하기 위한 가짜 서비스.

    결과 이미지는 입력을 흐리게 만든 뒤 'DEMO' 표시를 넣은 것이며 실제 AI 결과가 아니다.
    """

    def __init__(self, delay=1.2):
        self.delay = delay
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def reset_cancel(self):
        self._cancelled.clear()

    def generate(self, req: GenerationRequest) -> GenerationResult:
        started = time.monotonic()
        while time.monotonic() - started < self.delay:
            if self._cancelled.is_set():
                raise GenerationError("cancelled", "요청을 취소했습니다. (데모 모드 — 과금 없음)")
            time.sleep(0.05)
        if req.input_images:
            with Image.open(req.input_images[0]) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
        else:
            im = Image.new("RGB", (1024, 1024), "white")
        im.thumbnail((1024, 1024))
        im = ImageOps.grayscale(im).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, im.width, 34], fill=(40, 40, 40))
        d.text((8, 10), "DEMO - no API call was made (not an AI result)", fill=(255, 220, 80))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return GenerationResult(image_bytes=buf.getvalue(), model=req.model, request_id="demo", usage=None,
                                size=f"{im.width}x{im.height}", quality=req.quality, demo=True,
                                elapsed_sec=time.monotonic() - started)

    def list_image_models(self):
        return []

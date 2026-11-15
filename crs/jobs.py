"""생성 요청 1건의 공통 처리 (창 화면과 MCP 모드가 같이 쓴다).

검증 → (호출하는 쪽에서 비용 확인) → API 호출 → 결과 저장 → 사용량 기록.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import keystore
from .services.image_service import GenerationError, GenerationRequest, GenerationResult

log = logging.getLogger(__name__)


@dataclass
class GenJob:
    project_id: int
    function: str
    prompt: str
    preset_id: int | None
    preset_name: str
    input_paths: list[Path]
    source_image_id: int | None
    model: str
    quality: str = "auto"
    size: str = "auto"
    background: str = "auto"
    input_fidelity: str | None = None
    markup_path: Path | None = None
    extra_params: dict = field(default_factory=dict)
    source: str = "app"          # 'app' | 'mcp' (사용량 기록에 남는다)


def validate(ws, job: GenJob) -> list[str]:
    """보내기 전 확인. 문제가 있으면 사용자에게 보여 줄 문장 목록을 돌려준다."""
    if not ws.demo_mode and not keystore.get_key():
        return ["OpenAI API 키가 등록되어 있지 않습니다. 프로그램의 [설정 > API]에서 키를 등록하세요."]
    if not job.input_paths or not all(Path(p).exists() for p in job.input_paths):
        return ["입력 이미지가 없거나 파일을 찾을 수 없습니다."]
    if not job.model:
        return ["모델이 지정되지 않았습니다."]
    if not job.prompt.strip():
        return ["프롬프트가 비어 있습니다. [설정 > 프롬프트]에서 확인하세요."]
    problems = ws.catalog.validate(job.model, job.quality, job.size, job.background)
    if problems:
        return problems
    if job.markup_path and not ws.catalog.get(job.model).get("markup"):
        return [f"선택한 모델({job.model})은 빨간 펜 영역 지정(markup)을 지원하지 않습니다."]
    return []


def make_request(ws, job: GenJob) -> GenerationRequest:
    return GenerationRequest(input_images=[Path(p) for p in job.input_paths], prompt=job.prompt, model=job.model,
                             quality=job.quality, size=job.size, background=job.background,
                             input_fidelity=job.input_fidelity, markup=job.markup_path,
                             timeout_sec=float(ws.settings.get("timeout_sec")))


def _usage_fields(job: GenJob) -> dict:
    return dict(project_id=job.project_id, function=job.function, model=job.model, quality=job.quality,
                size=job.size, n_inputs=len(job.input_paths), source=job.source)


def record_success(ws, job: GenJob, result: GenerationResult) -> int:
    """결과 저장 + 사용량 기록. 저장 실패 시 GenerationError(save_failed)."""
    usage = result.usage
    cost = ws.catalog.actual_cost(job.model, usage) if not result.demo else 0.0
    params = {"input_fidelity": job.input_fidelity, "n_inputs": len(job.input_paths),
              "response_size": result.size, "response_quality": result.quality,
              "response_background": result.background, "elapsed_sec": round(result.elapsed_sec, 1),
              "demo": result.demo, **({"via": job.source} if job.source != "app" else {}), **job.extra_params}
    try:
        image_id = ws.save_output(
            job.project_id, result.image_bytes, function=job.function, source_image_id=job.source_image_id,
            prompt=job.prompt, preset_id=job.preset_id, preset_name=job.preset_name, model=job.model,
            quality=job.quality, size=job.size, background=job.background, params=params,
            request_id=result.request_id, usage=usage, est_cost_usd=cost)
    except Exception as e:
        log.exception("failed to save output")
        ws.db.add_usage(**_usage_fields(job), status="demo" if result.demo else "ok",
                        usage_json=None, est_cost_usd=cost, request_id=result.request_id,
                        message=f"결과 저장 실패: {type(e).__name__}")
        raise GenerationError("save_failed", f"이미지는 생성되었지만 저장에 실패했습니다: {e}\n(요청은 과금되었을 수 있습니다.)",
                              request_id=result.request_id) from e
    details = (usage or {}).get("input_tokens_details") or {}
    ws.db.add_usage(**_usage_fields(job), image_id=image_id, status="demo" if result.demo else "ok",
                    input_text_tokens=details.get("text_tokens"), input_image_tokens=details.get("image_tokens"),
                    output_tokens=(usage or {}).get("output_tokens"),
                    usage_json=json.dumps(usage) if usage else None, est_cost_usd=cost,
                    request_id=result.request_id,
                    message="데모 모드 (API 호출 없음)" if result.demo else
                    (None if usage else "API가 사용량을 반환하지 않아 비용을 계산하지 못했습니다."))
    log.info("generate ok: image_id=%s request_id=%s elapsed=%.1fs source=%s", image_id, result.request_id,
             result.elapsed_sec, job.source)
    return image_id


def record_failure(ws, job: GenJob, err: GenerationError) -> str:
    status = "unknown" if err.billing_uncertain else "failed"
    if ws.demo_mode:
        status = "failed"
    ws.db.add_usage(**_usage_fields(job), status=status, request_id=err.request_id,
                    message=f"[{err.kind}] {err.message}"[:500])
    log.warning("generate failed: kind=%s request_id=%s detail=%s", err.kind, err.request_id, err.detail)
    msg = err.message
    if err.request_id:
        msg += f"\n\n요청 ID: {err.request_id}"
    return msg


def run(ws, job: GenJob, service=None) -> int:
    """동기 실행 (MCP 모드용). 성공 시 결과 image_id, 실패 시 GenerationError."""
    problems = validate(ws, job)
    if problems:
        raise GenerationError("invalid", "\n".join(problems))
    service = service or ws.service
    service.reset_cancel()
    log.info("generate start: function=%s model=%s quality=%s size=%s inputs=%d demo=%s source=%s",
             job.function, job.model, job.quality, job.size, len(job.input_paths), ws.demo_mode, job.source)
    try:
        result = service.generate(make_request(ws, job))
    except GenerationError as e:
        record_failure(ws, job, e)
        raise
    except Exception as e:
        err = GenerationError("unknown", f"예상하지 못한 오류: {type(e).__name__}", detail=repr(e)[:500],
                              billing_uncertain=True)
        record_failure(ws, job, err)
        raise err from e
    return record_success(ws, job, result)

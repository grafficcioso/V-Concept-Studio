"""모델 카탈로그(옵션/요금표)와 비용 추정.

- 모델명·가격은 코드에 고정하지 않고 사용자 편집 가능한 models.json 에서 읽는다.
- 모든 금액은 '앱의 로컬 추정치'이며 OpenAI 청구액과 다를 수 있다.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .config import resource_dir

GENERIC_MODEL = {  # 카탈로그에 없는 모델: 옵션은 넓게 허용, 가격은 '알 수 없음'
    "qualities": ["auto", "low", "medium", "high"],
    "sizes": ["auto", "1024x1024", "1024x1536", "1536x1024"],
    "custom_size": True,
    "backgrounds": ["auto", "opaque", "transparent"],
    "input_fidelity": False,
    "markup": False,
    "pricing": {},
    "reference_per_image_usd": {},
    "note": "요금표에 없는 모델입니다. 옵션 지원 여부와 가격을 앱이 알 수 없습니다.",
}

DISCLAIMER = ("앱의 로컬 추정치입니다. 실제 청구액은 OpenAI 대시보드에서 확인하세요. "
              "입력 이미지·텍스트 토큰 등 추가 비용이 있을 수 있습니다.")


@dataclass
class Estimate:
    computable: bool
    typical: float | None = None
    low: float | None = None
    high: float | None = None
    basis: str = "none"            # 'history' | 'reference' | 'none'
    notes: list[str] = field(default_factory=list)

    def headline(self) -> str:
        if not self.computable:
            return "정확한 예상 비용 계산 불가"
        if self.low is not None and self.high is not None and abs(self.high - self.low) > 1e-6:
            return f"예상 약 ${self.typical:.3f} (범위 ${self.low:.3f} ~ ${self.high:.3f})"
        return f"예상 약 ${self.typical:.3f}"

    def text(self) -> str:
        return "\n".join([self.headline(), *("· " + n for n in self.notes)])

    def for_budget(self) -> float | None:
        if not self.computable:
            return None
        return self.high if self.high is not None else self.typical


class Catalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = {"verified_date": "", "sources": [], "models": []}
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("models"), list):
                self.data = data
                self._fill_missing_keys()
        except (OSError, ValueError):
            self.reset_to_default()

    def _fill_missing_keys(self):
        """구버전 models.json 에 새 항목(예: markup)이 없으면 내장 기본값의 같은 모델에서 채운다."""
        try:
            defaults = json.loads((resource_dir() / "defaults" / "models.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if "text_models" not in self.data:
            self.data["text_models"] = defaults.get("text_models", [])
        by_id = {m.get("id"): m for m in defaults.get("models", [])}
        for m in self.data["models"]:
            for k, v in by_id.get(m.get("id"), {}).items():
                m.setdefault(k, v)

    # ---- 텍스트 모델 (화풍 추출) -----------------------------------------
    def text_model_ids(self) -> list[str]:
        return [m["id"] for m in self.data.get("text_models", []) if m.get("id")]

    def text_model(self, model_id: str) -> dict:
        for m in self.data.get("text_models", []):
            if m.get("id") == model_id:
                return {"pricing": {}, "reasoning_effort": None, "label": model_id, "note": "", **m}
        return {"id": model_id, "label": model_id, "pricing": {}, "reasoning_effort": None,
                "note": "요금표에 없는 텍스트 모델입니다. 비용을 계산할 수 없습니다."}

    def text_cost(self, model_id: str, usage: dict | None) -> float | None:
        p = self.text_model(model_id).get("pricing") or {}
        if not usage or "input" not in p or "output" not in p:
            return None
        return ((usage.get("input_tokens") or 0) * p["input"] + (usage.get("output_tokens") or 0) * p["output"]) / 1e6

    def text_estimate(self, model_id: str, db=None) -> Estimate:
        p = self.text_model(model_id).get("pricing") or {}
        if "input" not in p or "output" not in p:
            return Estimate(False, notes=["이 텍스트 모델의 요금이 요금표에 없습니다.", DISCLAIMER])
        hist = db.text_usage_history(model_id) if db else []
        if hist:
            costs = [(i * p["input"] + o * p["output"]) / 1e6 for i, o in hist]
            return Estimate(True, typical=sum(costs) / len(costs), low=min(costs), high=max(costs), basis="history",
                            notes=[f"최근 {len(hist)}회 실제 사용량 기준", DISCLAIMER])
        # 기록이 없으면 넉넉한 가정치: 입력 3,000토큰(지시문+이미지), 출력 3,000토큰(추론 포함)
        typical = (3000 * p["input"] + 3000 * p["output"]) / 1e6
        return Estimate(True, typical=typical, low=typical / 3, high=typical * 2, basis="reference",
                        notes=["사용 기록이 없어 입력·출력 약 3,000토큰으로 가정한 값입니다.",
                               f"요금표 최종 확인일: {self.verified_date}", DISCLAIMER])

    def markup_models(self) -> list[str]:
        return [mid for mid in self.model_ids() if self.get(mid).get("markup")]

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def reset_to_default(self):
        shutil.copyfile(resource_dir() / "defaults" / "models.json", self.path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    @property
    def verified_date(self) -> str:
        return self.data.get("verified_date") or "미확인"

    def model_ids(self) -> list[str]:
        return [m["id"] for m in self.data["models"] if m.get("id")]

    def has(self, model_id) -> bool:
        return model_id in self.model_ids()

    def get(self, model_id: str) -> dict:
        for m in self.data["models"]:
            if m.get("id") == model_id:
                return {**GENERIC_MODEL, **m}
        return {**GENERIC_MODEL, "id": model_id, "label": model_id}

    def label(self, model_id) -> str:
        return self.get(model_id).get("label") or model_id

    def add_unknown_models(self, ids: list[str]) -> list[str]:
        added = []
        for mid in ids:
            if not self.has(mid):
                self.data["models"].append({**GENERIC_MODEL, "id": mid, "label": mid,
                                            "note": "서버 모델 목록에서 추가됨 — 옵션/가격을 직접 확인해 입력하세요."})
                added.append(mid)
        if added:
            self.save()
        return added

    # ---- 유효성 검증 -----------------------------------------------------
    def validate(self, model_id, quality, size, background) -> list[str]:
        m = self.get(model_id)
        problems = []
        if quality not in m["qualities"]:
            problems.append(f"품질 '{quality}'은(는) 이 모델에서 지원되지 않습니다.")
        if size not in m["sizes"]:
            if not m.get("custom_size"):
                problems.append(f"크기 '{size}'은(는) 이 모델에서 지원되지 않습니다.")
            else:
                err = check_custom_size(size)
                if err:
                    problems.append(err)
        if background not in m["backgrounds"]:
            problems.append(f"배경 옵션 '{background}'은(는) 이 모델에서 지원되지 않습니다.")
        return problems

    # ---- 비용 ------------------------------------------------------------
    def actual_cost(self, model_id, usage: dict | None) -> float | None:
        """API가 돌려준 사용량 × 요금표 = 앱 추정 실제 비용."""
        p = self.get(model_id).get("pricing") or {}
        if not usage or not all(k in p for k in ("text_input", "image_input", "image_output")):
            return None
        details = usage.get("input_tokens_details") or {}
        text_in = details.get("text_tokens")
        image_in = details.get("image_tokens")
        if text_in is None and image_in is None:  # 세부 내역이 없으면 높은 단가로 계산(보수적)
            text_in, image_in = 0, usage.get("input_tokens") or 0
        out = usage.get("output_tokens") or 0
        return ((text_in or 0) * p["text_input"] + (image_in or 0) * p["image_input"]
                + out * p["image_output"]) / 1_000_000

    def estimate(self, model_id, quality, size, prompt: str, n_inputs: int, db=None) -> Estimate:
        m = self.get(model_id)
        p = m.get("pricing") or {}
        if "image_output" not in p:
            return Estimate(False, notes=["이 모델의 요금이 요금표에 없습니다. [설정 > 모델·요금표]에서 입력할 수 있습니다.",
                                          DISCLAIMER])
        notes = []
        text_cost = (max(1, len(prompt) // 4) * p.get("text_input", 0)) / 1_000_000
        out_hist = db.output_token_history(model_id, quality, size) if db else []
        img_hist = db.input_image_token_history(model_id) if db else []
        img_cost = None
        if n_inputs and img_hist and "image_input" in p:
            img_cost = n_inputs * (sum(img_hist) / len(img_hist)) * p["image_input"] / 1_000_000

        if out_hist:
            rate = p["image_output"] / 1_000_000
            base = text_cost + (img_cost or 0)
            est = Estimate(True, typical=base + rate * sum(out_hist) / len(out_hist),
                           low=base + rate * min(out_hist), high=base + rate * max(out_hist), basis="history")
            notes.append(f"같은 모델·품질·크기의 최근 {len(out_hist)}회 실제 사용량 기록 기준")
        else:
            ref = (m.get("reference_per_image_usd") or {}).get(f"{quality}|{size}")
            if ref is None:
                return Estimate(False, notes=[
                    "이 모델·품질·크기 조합의 출력 토큰 수를 알 수 없습니다 (공식 표 없음, 사용 기록 없음).",
                    f"요금 단가: 이미지 출력 ${p['image_output']}/1M 토큰 · 이미지 입력 ${p.get('image_input', '?')}/1M"
                    f" · 텍스트 입력 ${p.get('text_input', '?')}/1M (최종 확인일 {self.verified_date})",
                    "한 번 생성하면 그 사용량 기록으로 다음부터 추정치를 표시합니다.", DISCLAIMER])
            est = Estimate(True, typical=float(ref) + text_cost + (img_cost or 0), basis="reference")
            notes.append("요금표의 장당 참고 단가(출력 이미지) 기준")
        if n_inputs and img_cost is None:
            notes.append(f"입력 이미지 {n_inputs}장의 토큰 비용은 포함되지 않았습니다 (기록 없음)")
        notes.append(f"요금표 최종 확인일: {self.verified_date}")
        notes.append(DISCLAIMER)
        est.notes = notes
        return est


def check_custom_size(size: str) -> str | None:
    """공식 문서의 임의 크기 규칙 검증. 문제 없으면 None."""
    m = re.fullmatch(r"(\d+)x(\d+)", size or "")
    if not m:
        return f"크기 형식이 잘못되었습니다: '{size}' (예: 1024x1536)"
    w, h = int(m.group(1)), int(m.group(2))
    if w % 16 or h % 16:
        return "가로·세로는 16의 배수여야 합니다."
    if max(w, h) > 3840:
        return "한 변은 3840px 이하여야 합니다."
    if max(w, h) / min(w, h) > 3:
        return "가로세로 비율은 1:3 ~ 3:1 사이여야 합니다."
    if not (655_360 <= w * h <= 8_294_400):
        return "총 픽셀 수는 655,360 ~ 8,294,400 사이여야 합니다."
    return None

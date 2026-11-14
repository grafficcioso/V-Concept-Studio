"""생성 기능 레지스트리 (플러그형).

새 생성 버튼을 추가하려면:
  1) defaults/prompts/<key>.md 에 기본 영문 프롬프트를 추가하고
  2) 아래 FUNCTIONS 에 GenFunction 항목을 추가한다.
UI(생성 버튼, 프롬프트 편집기, 프리셋)는 이 목록을 그대로 읽어 구성된다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenFunction:
    key: str
    label: str
    description: str
    primary: bool = False       # 메인 3개 버튼 여부
    show_in_panel: bool = True  # 생성 패널에 버튼 표시
    default_category: str = "Character"


FUNCTIONS: list[GenFunction] = [
    GenFunction("front_view", "정면화", "원화를 정면 머리 일러스트로 다시 그립니다.", primary=True,
                default_category="Character"),
    GenFunction("parts_separation", "파트 분할", "민머리·가발·액세서리 분리 시트를 만듭니다. "
                "결과는 한 장의 시트이며 개별 레이어가 아닙니다.", primary=True, default_category="Hair"),
    GenFunction("four_views", "사면도", "정면/좌/우/후면 4뷰 시트를 만듭니다. "
                "뷰 간 일관성은 완벽히 보장되지 않습니다.", primary=True, default_category="Character"),
    GenFunction("iris_restore", "홍채 복원", "눈·홍채를 크게 확대해 선명한 참조 이미지로 만듭니다.",
                default_category="Eyes"),
    GenFunction("upscale", "업스케일", "디자인을 바꾸지 않고 선명도를 높여 다시 그립니다.",
                default_category="Character"),
    GenFunction("retouch", "부분 수정", "결과에서 마음에 안 드는 영역을 빨간 펜으로 칠하고 수정 요청을 보냅니다. "
                "프롬프트의 {request}=요청 내용, {area_instruction}=영역 안내, {reference_instruction}=원본 참조 안내로 바뀝니다.",
                show_in_panel=False, default_category="Character"),
    GenFunction("part_views_hair", "헤어 사면도", "칠한 헤어(가발) 파트만 잘라 4방향 시트를 만듭니다. "
                "{reference_instruction}=원본 참조 안내.", show_in_panel=False, default_category="Hair"),
    GenFunction("part_views_head", "헤드 사면도", "칠한 민머리 헤드 파트만 잘라 4방향 시트를 만듭니다.",
                show_in_panel=False, default_category="Head"),
    GenFunction("part_views_accessory", "액세서리 사면도", "칠한 액세서리 파트만 잘라 4방향 시트를 만듭니다.",
                show_in_panel=False, default_category="Accessories"),
    GenFunction("part_views_other", "파트 사면도", "칠한 파트만 잘라 4방향 시트를 만듭니다.",
                show_in_panel=False, default_category="Character"),
    GenFunction("style_extract", "화풍 추출", "이미지의 화풍을 분석해 영문 화풍 프롬프트를 만드는 지시문입니다 "
                "(텍스트 모델 사용, 이미지 생성 아님).", show_in_panel=False, default_category="Character"),
    GenFunction("ai_compose", "AI 조합", "조합 화면의 미리보기와 부품을 참조해 새 일러스트를 생성합니다.",
                show_in_panel=False, default_category="Character"),
]

BY_KEY = {f.key: f for f in FUNCTIONS}

CATEGORIES = ["Head", "Hair", "Eyes", "Clothing", "Accessories", "Character"]
CATEGORY_LABELS = {
    "Head": "Head · 머리", "Hair": "Hair · 헤어", "Eyes": "Eyes-Iris · 눈/홍채",
    "Clothing": "Clothing · 의상", "Accessories": "Accessories · 액세서리",
    "Character": "Character · 캐릭터",
}


PART_TYPES = [  # (표시 이름, 기능 키)
    ("헤어 (가발)", "part_views_hair"),
    ("헤드 (민머리)", "part_views_head"),
    ("액세서리", "part_views_accessory"),
    ("기타 파트", "part_views_other"),
]

STYLE_SUFFIX = "\n\nArt style to follow (from a style analysis of the reference): {style}"


def fill_placeholders(template: str, **values) -> str:
    """프롬프트의 {name} 자리표시자를 바꾼다. 없는 자리표시자는 무시하고, 남은 이중 공백을 정리한다."""
    text = template
    for k, v in values.items():
        text = text.replace("{" + k + "}", v or "")
    return "\n".join(" ".join(line.split()) if line.strip() else "" for line in text.splitlines()).strip()


def label_of(key: str) -> str:
    f = BY_KEY.get(key)
    return f.label if f else (key or "")

"""입력 특징 정의와 벡터화 규칙 (UI 시안용 mock 전처리).

center / scale 은 임시 상수다. 팀이 데이터셋과 전처리 규칙(결측·스케일·정규화)을
확정하면 이 파일만 바꾸면 된다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    label: str  # 화면 표시용 한국어 이름
    en: str  # 제안서 표기
    unit: str
    min: int  # 입력 허용 범위 (입력 검증용이며 의학적 기준이 아니다)
    max: int
    example: int  # 제안서 예시 값
    center: float  # mock 스케일 상수
    scale: float


FEATURES: tuple[Feature, ...] = (
    Feature("age", "나이", "Age", "세", 0, 120, 52, 54.0, 9.0),
    Feature("resting_bp", "안정 시 혈압", "Resting BP", "mmHg", 50, 250, 145, 132.0, 18.0),
    Feature("cholesterol", "콜레스테롤", "Cholesterol", "mg/dL", 50, 700, 240, 245.0, 50.0),
    Feature("max_hr", "최대 심박수", "Max HR", "bpm", 40, 230, 130, 138.0, 24.0),
)

# 제안서 USE CASE 슬라이드의 검색 환자 Q
EXAMPLE_QUERY: dict[str, int] = {f.key: f.example for f in FEATURES}


def vectorize(values) -> tuple[list[float], float]:
    """특징값 → 스케일링 → L2 정규화.

    (단위 벡터, 정규화 전 노름)을 돌려준다. 0벡터면 노름이 0.0 이다.
    """
    z = [(float(values[f.key]) - f.center) / f.scale for f in FEATURES]
    norm = math.sqrt(sum(x * x for x in z))
    if norm < 1e-12:
        return [0.0] * len(z), 0.0
    return [x / norm for x in z], norm


def dot(a, b) -> float:
    """단위 벡터끼리의 내적 = 코사인 유사도."""
    return sum(x * y for x, y in zip(a, b))

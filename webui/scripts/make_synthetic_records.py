"""합성 기록(가상 환자) 생성 스크립트 — UI 시안용 mock 데이터.

실제 환자 데이터가 아니다. 시드를 고정해 항상 같은 파일이 나온다.

- 제안서 예시 질의(나이 52 · 혈압 145 · 콜레스테롤 240 · 최대 심박수 130)에 대해
  P023 0.971 · P104 0.923 · P056 0.881 이 Top-3가 되도록 세 기록을 고른다.
- 나머지 기록은 정규분포에서 뽑되, 예시 질의와의 유사도가 0.870을 넘으면 다시 뽑는다.
- P056과 번호가 10의 배수인 기록에는 '열람 제한' 표시를 붙인다 (접근 제어 mock).

사용:  webui/.venv/Scripts/python webui/scripts/make_synthetic_records.py
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.features import EXAMPLE_QUERY, FEATURES, dot, vectorize  # noqa: E402

SEED = 20261112
N_RECORDS = 200
TARGETS = {23: 0.971, 104: 0.923, 56: 0.881}  # 제안서 예시 Top-3
OTHERS_MAX = 0.870  # 나머지 기록의 예시 질의 유사도 상한
RESTRICTED_EXTRA = {56}  # 기본 화면(Top-3)에서 '권한 없음' 상태를 볼 수 있게 한다
CLIP = {
    "age": (29, 80),
    "resting_bp": (90, 200),
    "cholesterol": (120, 420),
    "max_hr": (70, 200),
}
OUT = ROOT / "app" / "data" / "synthetic_records.csv"


def draw(rng: random.Random) -> dict[str, int]:
    record = {}
    for f in FEATURES:
        low, high = CLIP[f.key]
        record[f.key] = min(high, max(low, round(rng.gauss(f.center, f.scale))))
    return record


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    rng = random.Random(SEED)
    query, _ = vectorize(EXAMPLE_QUERY)

    def cosine(record):
        vector, norm = vectorize(record)
        return dot(query, vector) if norm else None

    records: dict[int, dict[str, int]] = {}
    for number, target in TARGETS.items():
        for _ in range(2_000_000):
            candidate = draw(rng)
            score = cosine(candidate)
            if score is not None and abs(score - target) < 0.0004:
                records[number] = candidate
                break
        else:
            raise SystemExit(f"P{number:03d}: 목표 유사도 {target} 인 기록을 만들지 못했습니다")

    for number in range(1, N_RECORDS + 1):
        if number in records:
            continue
        while True:
            candidate = draw(rng)
            score = cosine(candidate)
            if score is not None and score <= OTHERS_MAX:
                records[number] = candidate
                break

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", *[f.key for f in FEATURES], "restricted"])
        for number in range(1, N_RECORDS + 1):
            restricted = int(number in RESTRICTED_EXTRA or number % 10 == 0)
            writer.writerow(
                [f"P{number:03d}", *[records[number][f.key] for f in FEATURES], restricted]
            )

    print(f"{OUT} 에 {N_RECORDS}건을 썼습니다. 예시 질의 상위 6건:")
    ranked = sorted(((cosine(r), n) for n, r in records.items()), reverse=True)[:6]
    for score, number in ranked:
        print(f"  P{number:03d}  {score:.3f}  {records[number]}")


if __name__ == "__main__":
    main()

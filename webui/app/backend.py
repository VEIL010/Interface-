"""검색 백엔드 — 현재는 평문 기준 계산 mock.

UI 시안을 띄우기 위한 대체 구현이다. 합성 기록에 평문 코사인 유사도를 계산한다.
CKKS 암호화 · Input Expansion · HE-C³ 매칭 · 복호화는 아직 연결되지 않았고,
연결되지 않은 단계는 응답의 pipeline[].status 에 "not_connected" 로 그대로 보고한다.
화면은 이 상태값을 받아 그대로 그리므로, 실제 암호화 경로를 붙일 때는
search() 가 돌려주는 값만 바꾸면 된다.

구조상 이 웹 서버는 제안서 그림의 Client(의료기관) 쪽이다. 브라우저에서 받은 평문
입력은 여기서 전처리·암호화하고, HE 서버에는 Enc(q)만 보내는 것이 목표 구조다.
"""
from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .features import EXAMPLE_QUERY, FEATURES, dot, vectorize

DATA_PATH = Path(__file__).resolve().parent / "data" / "synthetic_records.csv"

BACKEND_INFO = {
    "mode": "plaintext-mock",
    "encrypted": False,
    "label": "MOCK · 평문 기준 계산",
    "note": (
        "CKKS 암호화와 HE-C³ 매칭은 아직 연결되지 않았습니다. "
        "합성 기록에 평문 코사인 유사도를 계산합니다."
    ),
}

METHODS = (
    {"key": "plaintext", "label": "Plaintext", "role": "기준 검색", "available": True},
    {"key": "ckks_basic", "label": "Basic CKKS", "role": "암호화 내적", "available": False},
    {"key": "he_c3", "label": "HE-C³", "role": "분할·패킹 최적화", "available": False},
)

TOP_K_OPTIONS = (3, 5, 10)
DEFAULT_TOP_K = 3

# 기록 접근 제어 mock 의 요청자
REQUESTER = {"id": "DEMO-01", "label": "데모 의료진"}

# (key, actor, label, detail) — 제안서 "Blind-Match APPLICATION" 슬라이드의 흐름
STAGES = (
    ("preprocess", "client", "전처리", "특징 스케일링 후 L2 정규화"),
    ("encrypt", "client", "CKKS 암호화", "질의 벡터를 Enc(q)로 암호화"),
    ("expand", "server", "Input Expansion", "Mask + Rotation으로 부분 암호문 복제"),
    ("match", "server", "HE-C³ Matching", "암호화된 등록 벡터와 요소별 곱셈·합산"),
    ("decrypt", "client", "점수 복호화", "Enc(점수)를 유사도 점수로 복호화"),
    ("topk", "client", "Top-K 선택", "점수가 높은 기록 ID K개 선택"),
)
ACTOR_LABEL = {"client": "클라이언트", "server": "서버"}
STATUS_LABEL = {"done": "완료", "substituted": "평문 대체", "not_connected": "미연결"}

# 평문 mock 에서 각 단계가 실제로 무엇을 했는지
_PLAINTEXT_STATUS = {
    "preprocess": "done",
    "encrypt": "not_connected",
    "expand": "not_connected",
    "match": "substituted",
    "decrypt": "not_connected",
    "topk": "done",
}
_SUBSTITUTED_DETAIL = {"match": "평문 내적으로 대체 계산 (HE-C³ 미연결)"}

# 논문 보고값 — 우리 팀의 측정값이 아니다
PAPER_REFERENCE = {
    "source": "Blind-Match 논문 보고값 · IJB-C · 128차원 · 등록 6,144건",
    "matching_ms": [
        {"label": "HE-Match (기존 방식)", "ms": 1577.90, "text": "1,577.90"},
        {"label": "Blind-Match (4분할)", "ms": 451.67, "text": "451.67"},
    ],
    "total_ms_by_split": [
        {"split": 2, "ms": 942.59, "text": "942.59"},
        {"split": 4, "ms": 737.41, "text": "737.41"},
        {"split": 8, "ms": 740.20, "text": "740.20"},
        {"split": 16, "ms": 940.84, "text": "940.84"},
    ],
}

KEY_HOLDERS = [
    {
        "actor": "client",
        "label": "클라이언트 (의료기관)",
        "holds": ["Secret Key", "Public Key"],
        "note": "비밀키는 의료기관 클라이언트 밖으로 나가지 않습니다.",
    },
    {
        "actor": "server",
        "label": "서버",
        "holds": ["Evaluation Key (Mult / Rotation)"],
        "note": "Secret Key 없음 · 암호문만 다룹니다.",
    },
    {
        "actor": "access",
        "label": "기록 접근 제어",
        "holds": [],
        "note": "키 없음 · ID만 처리합니다.",
    },
]

DISCLAIMER = "유사도 점수는 기록 간 벡터 유사성입니다. 의료적 진단이나 발병 확률이 아닙니다."


class SearchError(Exception):
    """검색을 실행할 수 없는 경우. status 는 HTTP 상태 코드."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Record:
    record_id: str
    values: dict
    vector: tuple
    restricted: bool


def _load_records() -> list[Record]:
    if not DATA_PATH.exists():
        raise RuntimeError(
            f"{DATA_PATH} 가 없습니다. scripts/make_synthetic_records.py 를 먼저 실행하세요."
        )
    records = []
    with DATA_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            values = {f.key: int(row[f.key]) for f in FEATURES}
            vector, norm = vectorize(values)
            if norm == 0.0:
                continue  # 0벡터 기록은 유사도 후보에서 제외
            records.append(
                Record(row["record_id"], values, tuple(vector), row["restricted"] == "1")
            )
    return records


RECORDS: list[Record] = _load_records()
BY_ID: dict[str, Record] = {record.record_id: record for record in RECORDS}
ACCESS_LOG: list[dict] = []


def default_form() -> dict:
    """제안서 예시 질의로 채운 기본 폼 값."""
    return {**EXAMPLE_QUERY, "top_k": DEFAULT_TOP_K, "method": "plaintext"}


def parse_form(data: dict) -> tuple[dict, dict]:
    """폼/JSON 입력 검증. (정리된 값, 필드별 오류 메시지)를 돌려준다.

    오류가 난 필드는 사용자가 넣은 값을 그대로 돌려줘 화면에 다시 보여줄 수 있게 한다.
    """
    form: dict = {}
    errors: dict[str, str] = {}

    for f in FEATURES:
        raw = data.get(f.key)
        form[f.key] = "" if raw is None else raw
        try:
            if isinstance(raw, bool):
                raise ValueError
            value = float(str(raw).strip())
            if not math.isfinite(value):
                raise ValueError
        except (TypeError, ValueError):
            errors[f.key] = "숫자를 입력하세요."
            continue
        if not f.min <= value <= f.max:
            errors[f.key] = f"{f.min}–{f.max} {f.unit} 범위로 입력하세요."
            continue
        form[f.key] = int(value) if value == int(value) else value

    try:
        top_k = int(str(data.get("top_k", DEFAULT_TOP_K)).strip())
    except (TypeError, ValueError):
        top_k = None
    if top_k in TOP_K_OPTIONS:
        form["top_k"] = top_k
    else:
        form["top_k"] = DEFAULT_TOP_K
        errors["top_k"] = "Top-K는 " + " · ".join(str(k) for k in TOP_K_OPTIONS) + " 중에서 고르세요."

    method = str(data.get("method", "plaintext"))
    if method in {m["key"] for m in METHODS}:
        form["method"] = method
    else:
        form["method"] = "plaintext"
        errors["method"] = "지원하지 않는 방식입니다."

    return form, errors


def _ms_text(ms: float | None) -> str | None:
    return None if ms is None else f"{ms:.2f} ms"


def search(form: dict) -> dict:
    """유사 기록 Top-K 검색. 지금은 평문 코사인 유사도로 계산한다."""
    method = form["method"]
    if method != "plaintext":
        label = next(m["label"] for m in METHODS if m["key"] == method)
        raise SearchError(
            f"{label} 방식은 아직 연결되지 않았습니다. 지금은 Plaintext 기준 검색만 실행할 수 있습니다.",
            status=501,
        )

    values = {f.key: form[f.key] for f in FEATURES}
    t0 = time.perf_counter()
    query, norm = vectorize(values)
    t1 = time.perf_counter()
    if norm == 0.0:
        raise SearchError(
            "입력값이 스케일 기준값과 모두 같아 벡터 방향을 계산할 수 없습니다(0벡터). "
            "값을 하나 이상 바꿔 주세요."
        )
    scored = [(dot(query, record.vector), record.record_id) for record in RECORDS]
    t2 = time.perf_counter()
    top = sorted(scored, key=lambda item: (-item[0], item[1]))[: form["top_k"]]
    t3 = time.perf_counter()

    measured = {
        "preprocess": (t1 - t0) * 1000,
        "match": (t2 - t1) * 1000,
        "topk": (t3 - t2) * 1000,
    }
    pipeline = []
    for key, actor, label, detail in STAGES:
        status = _PLAINTEXT_STATUS[key]
        ms = measured.get(key)
        pipeline.append(
            {
                "key": key,
                "actor": actor,
                "actor_label": ACTOR_LABEL[actor],
                "label": label,
                "detail": _SUBSTITUTED_DETAIL.get(key, detail) if status == "substituted" else detail,
                "status": status,
                "status_label": STATUS_LABEL[status],
                "ms": ms,
                "ms_text": _ms_text(ms),
            }
        )
    total_ms = sum(measured.values())

    return {
        "method": method,
        "method_label": next(m["label"] for m in METHODS if m["key"] == method),
        "top_k": form["top_k"],
        "searched": len(RECORDS),
        # 모든 단계가 실제 암호화 경로로 실행됐는지 (mock 에서는 False)
        "all_connected": all(stage["status"] == "done" for stage in pipeline),
        "query": {
            # 주의: Jinja 에서 dict 의 values/keys/items 는 메서드로 잡히므로 키 이름으로 쓰지 않는다
            "inputs": [
                {"key": f.key, "label": f.label, "unit": f.unit, "value": values[f.key]}
                for f in FEATURES
            ],
            "vector": query,
            "vector_text": [f"{x:.3f}" for x in query],
            "dim": len(query),
        },
        "results": [
            {
                "rank": rank,
                "record_id": record_id,
                "score": score,
                "score_text": f"{score:.3f}",
                "bar_pct": max(0, min(100, round(score * 100))),
            }
            for rank, (score, record_id) in enumerate(top, start=1)
        ],
        "pipeline": pipeline,
        "timing": {"total_ms": total_ms, "total_text": _ms_text(total_ms)},
        "backend": BACKEND_INFO,
    }


def request_access(record_id: str) -> dict:
    """기록 접근 제어 mock: ID 권한을 확인하고 접근 기록을 남긴다.

    없는 ID면 KeyError.
    """
    record = BY_ID[record_id]
    granted = not record.restricted
    now = datetime.now()
    ACCESS_LOG.append(
        {
            "record_id": record_id,
            "requester_id": REQUESTER["id"],
            "granted": granted,
            "at": now.isoformat(timespec="seconds"),
        }
    )
    return {
        "record_id": record_id,
        "found": True,
        "granted": granted,
        "reason": None if granted else "요청자 ID에 이 기록의 열람 권한이 없습니다.",
        "fields": [
            {"key": f.key, "label": f.label, "unit": f.unit, "value": record.values[f.key]}
            for f in FEATURES
        ]
        if granted
        else [],
        "requester": REQUESTER,
        "logged_at": now.isoformat(timespec="seconds"),
        "logged_at_text": now.strftime("%Y-%m-%d %H:%M:%S"),
        "log_count": len(ACCESS_LOG),
    }


def unknown_record(record_id: str) -> dict:
    """없는 기록 ID를 요청했을 때 화면에 넘길 값."""
    return {
        "record_id": record_id,
        "found": False,
        "granted": False,
        "reason": "존재하지 않는 기록 ID입니다.",
        "fields": [],
        "requester": REQUESTER,
        "logged_at": None,
        "logged_at_text": None,
        "log_count": len(ACCESS_LOG),
    }


def meta() -> dict:
    """화면이 공통으로 쓰는 고정 정보."""
    return {
        "service": {"name": "Blind-Match", "subtitle": "의료 유사 기록 검색"},
        "backend": BACKEND_INFO,
        "features": [
            {
                "key": f.key,
                "label": f.label,
                "en": f.en,
                "unit": f.unit,
                "min": f.min,
                "max": f.max,
                "example": f.example,
            }
            for f in FEATURES
        ],
        "top_k_options": list(TOP_K_OPTIONS),
        "default_top_k": DEFAULT_TOP_K,
        "methods": list(METHODS),
        "dataset": {
            "label": "합성 데이터 (가상 환자)",
            "records": len(RECORDS),
            "feature_dim": len(FEATURES),
        },
        "requester": REQUESTER,
        "stages": [
            {
                "key": key,
                "actor": actor,
                "actor_label": ACTOR_LABEL[actor],
                "label": label,
                "detail": detail,
            }
            for key, actor, label, detail in STAGES
        ],
        "key_holders": KEY_HOLDERS,
        "metrics": ["Top-K 일치율", "시간", "메모리", "통신량", "CKKS 점수 오차"],
        "paper_reference": PAPER_REFERENCE,
        "disclaimer": DISCLAIMER,
    }

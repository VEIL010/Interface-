"""13개 의료 속성 질의: 입력 검증 → 벡터화 → Medical-Search-API 호출.

이 서버는 의료기관(클라이언트) 역할이다. 원본 속성은 여기서 벡터로 바꾸고,
검색 서버에는 벡터만 보낸다. 지금은 평문 기준 검색(scheme=plaintext)만 연결되어 있다.

환경 변수
    VEIL_SEARCH_API_URL   Medical-Search-API 주소 (예: http://127.0.0.1:8200). 없으면 검색을 켜지 않는다.
    VEIL_SEARCH_API_KEY   API 가 키를 요구할 때 X-API-Key 로 보낸다.
    VEIL_PIPELINE_DIR     Medical-Data-Pipeline 산출물 폴더. 기본값은 webui/.pipeline-data.

preprocessor.joblib 은 pickle 이다. 팀이 직접 만든 파일만 둔다.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path

import httpx

from .heart_fields import FIELDS

DEFAULT_PIPELINE_DIR = Path(__file__).resolve().parents[1] / ".pipeline-data"
TOP_K_OPTIONS = (3, 5, 10)
DEFAULT_TOP_K = 5
TIMEOUT_SECONDS = 5.0
MISSING = "__MISSING__"

# 테스트에서 가짜 검색 서버를 끼울 때 쓴다 (httpx.MockTransport)
transport: httpx.BaseTransport | None = None


class SearchUnavailable(RuntimeError):
    """검색 서버에 닿지 못했거나 서버가 요청을 처리하지 못했다."""


def api_url() -> str | None:
    url = os.environ.get("VEIL_SEARCH_API_URL", "").strip().rstrip("/")
    return url or None


def pipeline_dir() -> Path:
    return Path(os.environ.get("VEIL_PIPELINE_DIR") or DEFAULT_PIPELINE_DIR)


def enabled() -> bool:
    """검색 서버 주소와 전처리기가 둘 다 있어야 검색을 켠다."""
    folder = pipeline_dir()
    return bool(api_url()) and (folder / "preprocessor.joblib").is_file() and (folder / "metadata.json").is_file()


# ───────────────────────── 입력 검증 ─────────────────────────


def parse(data: dict) -> tuple[dict[str, str], dict, int, dict[str, str]]:
    """(화면에 되돌려 줄 값, 검증된 값, top_k, 필드별 오류)를 돌려준다.

    검증된 값에서 수치형은 float, 범주형은 선택한 옵션 값(str)이고, 비워 둔 선택 입력은 None 이다.
    """
    form: dict[str, str] = {}
    values: dict = {}
    errors: dict[str, str] = {}

    for field in FIELDS:
        key = field["key"]
        text = str(data.get(key, "") or "").strip()
        form[key] = text
        if not text:
            if field["required"]:
                errors[key] = "선택하세요." if field["options"] else "값을 입력하세요."
            values[key] = None
            continue
        if field["options"]:
            if text not in {value for value, _ in field["options"]}:
                errors[key] = "목록에 있는 값을 선택하세요."
            values[key] = text
            continue
        try:
            number = float(text)
        except ValueError:
            number = math.nan
        if not math.isfinite(number):
            errors[key] = "숫자를 입력하세요."
        elif field["min"] is not None and number < field["min"]:
            errors[key] = f"{field['min']} 이상이어야 합니다."
        elif field["max"] is not None and number > field["max"]:
            errors[key] = f"{field['max']} 이하여야 합니다."
        elif field["step"] == "1" and not number.is_integer():
            errors[key] = "정수를 입력하세요."
        values[key] = number

    try:
        top_k = int(data.get("top_k", DEFAULT_TOP_K))
    except (TypeError, ValueError):
        top_k = 0
    if top_k not in TOP_K_OPTIONS:
        errors["top_k"] = "결과 개수를 선택하세요."
        top_k = DEFAULT_TOP_K
    return form, values, top_k, errors


# ───────────────────────── 벡터화 ─────────────────────────


@lru_cache(maxsize=1)
def _preprocessor(folder: str):
    import joblib  # 검색을 켠 경우에만 필요하다

    path = Path(folder)
    metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    transformer = joblib.load(path / "preprocessor.joblib")
    numeric = list(metadata["numeric_columns"])
    categorical = list(metadata["categorical_columns"])
    if sorted(numeric + categorical) != sorted(field["key"] for field in FIELDS):
        raise RuntimeError("전처리기의 입력 열과 화면의 입력란이 다릅니다.")
    encoder = transformer.named_transformers_["categorical"].named_steps["encode"]
    categories = {column: list(cats) for column, cats in zip(categorical, encoder.categories_)}
    return transformer, numeric, categorical, categories, int(metadata["padded_dimension"])


def _category(column: str, value: str | None, known: list[str]) -> str:
    """화면의 옵션 값을 전처리기가 학습한 범주 표기로 바꾼다.

    파이프라인은 범주형 열을 문자열로 바꿔 학습하므로, 원본이 실수였던 열(thal)은 범주가
    "3.0" 처럼 저장되어 있다. "3" 을 그대로 넘기면 모르는 범주로 취급되어 조용히 0 으로 인코딩된다.
    """
    if value is None:
        return MISSING
    for candidate in known:
        try:
            if float(candidate) == float(value):
                return candidate
        except ValueError:
            continue
    raise RuntimeError(f"전처리기가 모르는 범주입니다: {column}")


def vectorize(values: dict) -> list[float]:
    """파이프라인과 같은 순서로 변환한다: 결측 대체·스케일·원-핫 → 0 채움 → L2 정규화."""
    import numpy as np
    import pandas as pd

    transformer, numeric, categorical, categories, dimension = _preprocessor(str(pipeline_dir()))
    row = {column: (math.nan if values[column] is None else float(values[column])) for column in numeric}
    row.update({column: _category(column, values[column], categories[column]) for column in categorical})
    frame = pd.DataFrame([row], columns=numeric + categorical)

    encoded = np.asarray(transformer.transform(frame), dtype=np.float64)[0]
    if encoded.shape[0] > dimension:
        raise RuntimeError("인코딩된 벡터가 등록 차원보다 깁니다.")
    vector = np.zeros(dimension, dtype=np.float64)
    vector[: encoded.shape[0]] = encoded
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-12:
        raise RuntimeError("질의 벡터를 정규화할 수 없습니다.")
    return (vector / norm).tolist()


# ───────────────────────── 검색 서버 호출 ─────────────────────────


def search(vector: list[float], top_k: int) -> dict:
    url = api_url()
    if url is None:
        raise SearchUnavailable("검색 API 주소가 설정되지 않았습니다.")
    headers = {}
    key = os.environ.get("VEIL_SEARCH_API_KEY")
    if key:
        headers["X-API-Key"] = key
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(
                f"{url}/v1/search",
                json={"scheme": "plaintext", "vector": vector, "top_k": top_k},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise SearchUnavailable("검색 서버에 연결하지 못했습니다.") from exc
    if response.status_code != 200:
        raise SearchUnavailable(f"검색 서버가 요청을 처리하지 못했습니다 (HTTP {response.status_code}).")
    try:
        body = response.json()
        hits = [
            {"rank": int(hit["rank"]), "record_id": str(hit["record_id"]), "score": float(hit["score"])}
            for hit in body["results"]
        ]
        return {"hits": hits, "searched": int(body["searched"]), "encrypted": bool(body["encrypted"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise SearchUnavailable("검색 서버의 응답을 해석하지 못했습니다.") from exc


# ───────────────────────── 화면용 결과 ─────────────────────────


def _display(field: dict, value) -> str:
    if value is None:
        return "미입력"
    if field["options"]:
        return dict(field["options"])[value]
    return format(value, "g")


def present(form: dict[str, str], values: dict, top_k: int, found: dict) -> dict:
    """A 화면의 결과 조각(_results.html)이 그리는 모양으로 맞춘다."""
    return {
        "top_k": top_k,
        "searched": found["searched"],
        "encrypted": found["encrypted"],
        "query": {
            "inputs": [
                {
                    "key": field["key"],
                    "label": field["label"],
                    "value": form[field["key"]],  # 입력란의 글자 그대로 — 화면이 "지난 결과"인지 비교한다
                    "text": _display(field, values[field["key"]]),
                    "unit": field["unit"] if values[field["key"]] is not None else "",
                }
                for field in FIELDS
            ]
        },
        "results": [
            {
                "rank": hit["rank"],
                "record_id": hit["record_id"],
                "score_text": f"{hit['score']:.3f}",
                "bar_pct": round(min(max(hit["score"], 0.0), 1.0) * 100, 1),
            }
            for hit in found["hits"]
        ],
    }

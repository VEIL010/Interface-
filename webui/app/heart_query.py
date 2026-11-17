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
import threading
import time
from functools import lru_cache
from pathlib import Path

import httpx

from .backend import ACTOR_LABEL, STAGES, STATUS_LABEL
from .heart_fields import FIELDS

DEFAULT_PIPELINE_DIR = Path(__file__).resolve().parents[1] / ".pipeline-data"
TOP_K_OPTIONS = (3, 5, 10)
DEFAULT_TOP_K = 5
TIMEOUT_SECONDS = 5.0
META_TIMEOUT_SECONDS = 1.5   # 화면을 그릴 때 등록 DB 정보를 묻는 요청. 길게 기다리지 않는다
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
    # 첫 변환에만 드는 준비 비용(라이브러리 내부 초기화)을 여기서 한 번 치른다.
    # B·D 가 보여 주는 질의 한 건의 전처리 시간에 이 비용이 섞이지 않게 하려는 예열이라, 실패해도 무시한다.
    try:
        import pandas as pd

        dummy = {column: math.nan for column in numeric}
        dummy.update({column: categories[column][0] for column in categorical if categories[column]})
        transformer.transform(pd.DataFrame([dummy], columns=numeric + categorical))
    except Exception:  # noqa: BLE001
        pass
    return transformer, numeric, categorical, categories, int(metadata["padded_dimension"])


def warm_up() -> None:
    """전처리기 적재 · 라이브러리 import · 첫 변환 준비를 미리 끝낸다 (처음 한 번만 오래 걸린다).

    B·D 화면은 질의 한 건의 전처리 시간을 보여 주므로, 이 한 번뿐인 비용은 측정에서 뺀다.
    전처리기를 읽지 못하면 vectorize() 와 같은 RuntimeError 를 낸다.
    """
    import numpy  # noqa: F401  vectorize() 가 쓰는 라이브러리
    import pandas  # noqa: F401

    _preprocessor(str(pipeline_dir()))


def dimension() -> int | None:
    """등록 벡터 차원 (metadata.json 의 padded_dimension). 읽지 못하면 None."""
    try:
        metadata = json.loads((pipeline_dir() / "metadata.json").read_text(encoding="utf-8"))
        return int(metadata["padded_dimension"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


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


def _headers() -> dict[str, str]:
    key = os.environ.get("VEIL_SEARCH_API_KEY")
    return {"X-API-Key": key} if key else {}


@lru_cache(maxsize=1)
def _ssl_context():
    """TLS 인증서 묶음은 한 번만 읽는다. 클라이언트를 만들 때마다 읽으면 요청마다 수백 ms 가 든다."""
    return httpx.create_ssl_context()


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=transport, verify=_ssl_context())


def _non_negative(value) -> float | None:
    """검색 API 가 보고한 시간(ms). 숫자가 아니거나 음수 · 무한대면 None (화면은 대신 요청 왕복을 쓴다)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def search(vector: list[float], top_k: int) -> dict:
    url = api_url()
    if url is None:
        raise SearchUnavailable("검색 API 주소가 설정되지 않았습니다.")
    try:
        with _client(TIMEOUT_SECONDS) as client:
            started = time.perf_counter()
            response = client.post(
                f"{url}/v1/search",
                json={"scheme": "plaintext", "vector": vector, "top_k": top_k},
                headers=_headers(),
            )
            round_trip_ms = (time.perf_counter() - started) * 1000
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
        return {
            "hits": hits,
            "searched": int(body["searched"]),
            "encrypted": bool(body["encrypted"]),
            # 아래는 B·D 화면의 측정값. 검색 API 가 보고하지 않으면 None 으로 둔다
            "dataset": str(body["dataset"]) if body.get("dataset") else None,
            "server_ms": _non_negative(body.get("elapsed_ms")),
            "round_trip_ms": round_trip_ms,
            # 통신량은 HTTP 본문 크기다 (헤더 제외)
            "request_bytes": len(response.request.content),
            "response_bytes": len(response.content),
        }
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise SearchUnavailable("검색 서버의 응답을 해석하지 못했습니다.") from exc


# 등록 DB 정보는 화면을 열 때마다 묻지 않고 잠시 기억한다. 못 읽은 결과는 짧게만 기억한다.
_META_TTL_SECONDS = 60.0
_META_FAILURE_TTL_SECONDS = 5.0
_meta_cache: dict[tuple, tuple[float, dict | None]] = {}
_meta_lock = threading.Lock()


def dataset_info() -> dict | None:
    """검색 API 의 등록 DB 정보 {name, records, dimension}. 닿지 않거나 형식이 다르면 None.

    화면(D 의 머리, B 의 등록 DB)에만 쓴다. 검색 자체에는 필요 없다.
    실제 연결(transport 없음)일 때만 기억해 둔다. 테스트처럼 transport 를 바꿔 끼우면 매번 묻는다.
    """
    url = api_url()
    if url is None:
        return None
    key = (url, os.environ.get("VEIL_SEARCH_API_KEY", ""))
    if transport is None:
        with _meta_lock:
            cached = _meta_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    info = _fetch_dataset_info(url)
    if transport is None:
        ttl = _META_TTL_SECONDS if info else _META_FAILURE_TTL_SECONDS
        with _meta_lock:
            _meta_cache[key] = (time.monotonic() + ttl, info)
    return info


def _fetch_dataset_info(url: str) -> dict | None:
    try:
        with _client(META_TIMEOUT_SECONDS) as client:
            response = client.get(f"{url}/v1/meta", headers=_headers())
        if response.status_code != 200:
            return None
        dataset = response.json()["dataset"]
        return {"name": str(dataset["name"]), "records": int(dataset["records"]), "dimension": int(dataset["dimension"])}
    except (httpx.HTTPError, KeyError, TypeError, ValueError, OverflowError):
        return None


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


# ───────────────────────── B·D 화면용: 단계 상태와 측정값 ─────────────────────────

SEARCH_API_BACKEND = {
    "mode": "search-api-plaintext",
    "encrypted": False,
    "label": "평문 검색 · Medical-Search-API",
    "note": (
        "CKKS 암호화와 HE-C³ 매칭은 아직 연결되지 않았습니다. "
        "13개 의료 속성을 이 서버에서 벡터로 바꾼 뒤 Medical-Search-API에서 평문 코사인 유사도로 검색합니다."
    ),
}

# 실제 검색 경로에서 각 단계가 한 일. 합성 데모(backend._PLAINTEXT_STATUS)와 달리
# Top-K 는 의료기관이 아니라 검색 서버가 평문 점수로 고른다.
_SEARCH_API_STATUS = {
    "preprocess": "done",
    "encrypt": "not_connected",
    "expand": "not_connected",
    "match": "substituted",
    "decrypt": "not_connected",
    "topk": "substituted",
}
_SEARCH_API_DETAIL = {
    "preprocess": "13개 의료 속성 · 저장된 전처리기 · L2 정규화",
    "match": "Medical-Search-API 평문 코사인 유사도로 대체 (HE-C³ 미연결)",
    "topk": "검색 서버가 평문 점수로 골라 돌려줌 · 시간은 매칭에 포함",
}
# 목표 구조에서는 의료기관이 하는 단계지만 지금은 검색 서버가 한다. 화면의 '수행 위치'만 바꾸고
# actor 는 그대로 둔다 (B 는 actor 로 카드를 놓을 레인을 정한다).
_SEARCH_API_ACTOR_LABEL = {"topk": "검색 서버 (대체)"}


def _ms_text(ms: float | None) -> str | None:
    return None if ms is None else f"{ms:.2f} ms"


def _size_text(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def with_pipeline(result: dict, vector: list[float], found: dict, preprocess_ms: float) -> dict:
    """present() 결과에 B·D 화면이 그리는 값(단계 상태 · 측정 시간 · 질의 벡터)을 더한다.

    합성 데모(backend.search)와 같은 모양이라 B·D 의 결과 조각을 그대로 쓴다. 측정값은 셋이다:
    전처리(이 서버) · 매칭(검색 API 가 보고한 계산 시간, 없으면 요청 왕복) · 요청 왕복(전송 포함).
    """
    server_ms = found.get("server_ms")
    round_trip_ms = found.get("round_trip_ms")
    match_ms = server_ms if server_ms is not None else round_trip_ms
    measured = {"preprocess": preprocess_ms, "match": match_ms}
    pipeline = []
    for key, actor, label, detail in STAGES:
        status = _SEARCH_API_STATUS[key]
        ms = measured.get(key)
        detail = _SEARCH_API_DETAIL.get(key, detail)
        if key == "match" and server_ms is None and ms is not None:
            # 검색 API 가 계산 시간을 보고하지 않으면 전송까지 포함한 왕복 시간이라는 것을 그 자리에서 밝힌다
            detail += " · 서버 계산 시간 미보고 → 요청 왕복(전송 포함)"
        pipeline.append({
            "key": key,
            "actor": actor,
            "actor_label": _SEARCH_API_ACTOR_LABEL.get(key, ACTOR_LABEL[actor]),
            "label": label,
            "detail": detail,
            "status": status,
            "status_label": STATUS_LABEL[status],
            "ms": ms,
            "ms_text": _ms_text(ms),
        })
    total_ms = sum(ms for ms in measured.values() if ms is not None)
    # 사용자가 실제로 기다린 시간에 가까운 값: 전처리 + 요청 왕복(전송 포함). 방식 비교의 '시간'에 쓴다
    end_to_end_ms = preprocess_ms + round_trip_ms if round_trip_ms is not None else None
    request_bytes, response_bytes = found.get("request_bytes"), found.get("response_bytes")
    return {
        **result,
        "method": "plaintext",
        "method_label": "Plaintext",
        "all_connected": False,
        "backend": SEARCH_API_BACKEND,
        "dataset": {"name": found.get("dataset") or "등록 DB", "records": found["searched"]},
        "query": {
            **result["query"],
            "vector": vector,
            "vector_text": [f"{x:.3f}" for x in vector],
            "dim": len(vector),
        },
        "pipeline": pipeline,
        "timing": {
            # 단계 합계: 전처리(이 서버) + 매칭(검색 API 계산 시간, 보고가 없으면 요청 왕복)
            "total_ms": total_ms,
            "total_text": _ms_text(total_ms),
            "server_measured": server_ms is not None,
            "round_trip_ms": round_trip_ms,
            "round_trip_text": _ms_text(round_trip_ms),
            "end_to_end_ms": end_to_end_ms,
            "end_to_end_text": _ms_text(end_to_end_ms),
        },
        "transfer": (
            {"request_bytes": request_bytes, "response_bytes": response_bytes,
             "text": f"요청 본문 {_size_text(request_bytes)} · 응답 본문 {_size_text(response_bytes)}"}
            if request_bytes is not None and response_bytes is not None else None
        ),
    }

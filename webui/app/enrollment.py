"""Institution-local CSV enrollment. Raw records and fitted transformers stay here.

This institution-side service supports plaintext testing only; CKKS is not connected.
The team's pipeline is imported read-only rather than forked or edited.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import math
import os
import secrets
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

import httpx
import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import heart_fields as fields_module

ROOT = Path(__file__).resolve().parent
MAX_CSV_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 2 * MAX_CSV_BYTES
MAX_ROWS = 2000
MAX_PREVIEWS = 8
PREVIEW_SECONDS = 1800


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _LazyPipeline:
    """Load the trusted sibling pipeline only when enrollment/encoding is requested."""
    def __init__(self):
        self.module = None
        self.path = None
        self.lock = threading.Lock()

    def __getattr__(self, name):
        path = Path(os.environ.get("VEIL_PIPELINE_SOURCE_DIR") or ROOT.parents[2] / "Medical-Data-Pipeline") / "preprocess.py"
        with self.lock:
            if self.module is None or self.path != path:
                try:
                    module = _load("veil_dataset_pipeline", path)
                except (OSError, ImportError):
                    raise InputError("Medical-Data-Pipeline을 같은 상위 폴더에 준비하거나 VEIL_PIPELINE_SOURCE_DIR을 설정해 주세요.", status=503) from None
                self.module, self.path = module, path
            return getattr(self.module, name)


pipeline = _LazyPipeline()
FIELDS = fields_module.FIELDS
KEYS = [field["key"] for field in FIELDS]
# 화면에서 입력란을 묶는 단위. Interface- 의료진 화면과 같은 묶음을 쓴다 (없으면 한 묶음).
GROUPS = [{"title": title, "keys": list(keys)} for title, keys in getattr(fields_module, "GROUPS", ())] \
    or [{"title": "의료 속성", "keys": KEYS}]
MISSING = "__MISSING__"


class InputError(Exception):
    # code: 화면이 문구 대신 기대는 안정된 이름 (예: "dataset_changed"). 없으면 응답에 넣지 않는다.
    def __init__(self, message: str, *, status: int = 422, issues=None, code: str | None = None):
        self.message, self.status, self.issues, self.code = message, status, issues or [], code


# 정답 레이블 · 식별자로 보이는 열 이름. 의료 속성에 연결하면 검증에서 거절한다 (화면도 같은 목록으로 미리 막는다).
BLOCKED_COLUMNS = {"target", "num", "record_id", "patient_id", "id"}


class NotSent(InputError):
    """요청이 수신 서버에 닿지 않았다 (연결 자체가 실패). 서버 쪽 상태는 바뀌지 않았다."""


class AmbiguousDelivery(InputError):
    """응답이 불확실하다. 서버가 요청을 이미 처리했거나 나중에 처리할 수 있다."""


def parse_csv(text: str):
    if not isinstance(text, str):
        raise InputError("CSV 텍스트가 필요합니다.")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise InputError("CSV는 UTF-8 기준 1 MB 이하여야 합니다.", status=413)
    if "\x00" in text:
        raise InputError("UTF-8 CSV 파일을 선택해 주세요.")
    try:
        reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), strict=True)
        columns = [cell.strip() for cell in next(reader)]
        if not columns or len(columns) > 64 or any(not c or len(c) > 100 for c in columns):
            raise InputError("첫 줄에는 비어 있지 않은 열 이름이 필요합니다. 최대 64열입니다.")
        if len(set(columns)) != len(columns):
            raise InputError("같은 열 이름이 중복되어 있습니다.")
        rows = []
        for number, row in enumerate(reader, 2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(columns):
                raise InputError("행마다 열 개수가 같아야 합니다.", issues=[{"row": number, "field": "", "message": "헤더와 열 개수가 다릅니다."}])
            if any(len(cell) > 256 for cell in row):
                raise InputError("셀 값은 256자 이하여야 합니다.")
            rows.append((number, dict(zip(columns, (cell.strip() for cell in row)))))
            if len(rows) > MAX_ROWS:
                raise InputError("로컬 테스트는 한 번에 2,000행까지 등록할 수 있습니다.", status=413)
        if not rows:
            raise InputError("헤더 외에 실제 데이터 행이 필요합니다. DB 구조만으로는 등록할 수 없습니다.")
        return columns, rows
    except (StopIteration, csv.Error, UnicodeError):
        raise InputError("CSV 형식을 읽지 못했습니다. UTF-8 CSV를 사용해 주세요.") from None


def normalize_row(raw: dict, row_number: int):
    normalized, issues = {}, []
    for field in FIELDS:
        key = field["key"]
        value = raw.get(key)
        text = "" if value is None else str(value).strip()
        if text in ("", "?"):
            normalized[key] = None
            continue
        reason = None
        try:
            number = float(text)
            if not math.isfinite(number) or abs(number) > 1_000_000:
                reason = "유한한 숫자여야 하며 절댓값은 1,000,000 이하여야 합니다."
            elif field["options"]:
                allowed = {v for v, _ in field["options"]}
                if not number.is_integer() or str(int(number)) not in allowed:
                    reason = "지원 코드: " + ", ".join(sorted(allowed))
                else:
                    normalized[key] = str(int(number))
            elif field["step"] == "1" and not number.is_integer():
                reason = "정수를 입력해 주세요."
            elif field["min"] is not None and number < field["min"]:
                reason = f"{field['min']} 이상이어야 합니다."
            elif field["max"] is not None and number > field["max"]:
                reason = f"{field['max']} 이하여야 합니다."
            else:
                normalized[key] = number
        except (TypeError, ValueError, OverflowError):
            reason = "숫자 또는 결측값(빈 셀, ?)을 사용해 주세요."
        if reason:
            issues.append({"row": row_number, "field": key, "message": reason})
    return normalized, issues


def _frame(rows):
    # Consistent canonical category strings avoid separate categories for 1 and 1.0.
    return pipeline.prepare_columns(pd.DataFrame(rows, columns=KEYS))


def create_app(data_dir: Path | None = None, receiver_url: str | None = None,
               api_key: str | None = None, transport=None, *, search_api_key: str | None = None):
    data_dir = Path(data_dir or os.environ.get("VEIL_DATASET_CLIENT_DIR", ROOT.parent / ".dataset-client"))
    receiver_url = (receiver_url or os.environ.get("VEIL_SEARCH_API_URL", "http://127.0.0.1:8200")).rstrip("/")
    try:
        parsed_url = httpx.URL(receiver_url)
        receiver_allowed = parsed_url.scheme == "http" and parsed_url.host in ("127.0.0.1", "localhost", "::1", "testserver")
    except httpx.InvalidURL:
        receiver_allowed = False
    enrollment_token = api_key if api_key is not None else (os.environ.get("VEIL_ENROLLMENT_API_KEY") or os.environ.get("VEIL_API_KEY", ""))
    search_token = search_api_key if search_api_key is not None else (api_key if api_key is not None else (os.environ.get("VEIL_SEARCH_API_KEY") or os.environ.get("VEIL_API_KEY", "")))
    previews = OrderedDict()
    preview_lock = threading.RLock()
    enrollment_lock = threading.Lock()
    app = FastAPI(title="VEIL · 기관 로컬 일괄 등록", version="0.2.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": {"message": "같은 로컬 화면에서 요청해 주세요."}}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": {"message": "JSON 요청이 필요합니다."}}, status_code=415)
            declared = request.headers.get("content-length")
            if declared and (not declared.isdigit() or int(declared) > MAX_REQUEST_BYTES):
                return JSONResponse({"error": {"message": "요청이 너무 큽니다."}}, status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_REQUEST_BYTES:
                    return JSONResponse({"error": {"message": "요청이 너무 큽니다."}}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # 글꼴(Pretendard)만 jsDelivr 에서 받는다. 스크립트·연결은 같은 출처로 제한한다.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' https://cdn.jsdelivr.net; font-src https://cdn.jsdelivr.net; "
            "script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(InputError)
    async def invalid(request, exc):
        error = {"message": exc.message, "issues": exc.issues}
        if exc.code:
            error["code"] = exc.code
        return JSONResponse({"error": error}, status_code=exc.status)

    async def body(request):
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise InputError("JSON 형식이 올바르지 않습니다.", status=400) from None
        if not isinstance(value, dict):
            raise InputError("JSON 객체가 필요합니다.")
        return value

    def remote(method, path, payload=None):
        if not receiver_allowed:
            raise InputError("평문 DB 등록·검색은 로컬 Medical-Search-API 주소(http://127.0.0.1:8200)를 사용해 주세요.", status=503)
        token = enrollment_token if path == "/v1/datasets" else search_token
        if path == "/v1/datasets" and not token:
            raise InputError("DB 등록 키가 없습니다. 검색 API와 같은 VEIL_ENROLLMENT_API_KEY 또는 VEIL_API_KEY를 설정해 주세요.", status=503)
        try:
            with httpx.Client(base_url=receiver_url, headers={"X-API-Key": token} if token else {},
                              timeout=30, transport=transport, trust_env=False) as client:
                res = client.request(method, path, json=payload)
            if res.status_code >= 400:
                try:
                    code = res.json()["error"]["code"]
                except (ValueError, KeyError, TypeError):
                    code = None
                # 검색 직전에 다른 화면이 새 등록본을 활성화한 경우. 화면은 code 로 알아보고 상태를 다시 읽는다.
                if code in ("dataset_mismatch", "dataset_not_registered", "dataset_changed"):
                    raise InputError("현재 등록본이 바뀌었습니다. 현재 등록본을 다시 확인해 주세요.",
                                     status=409, code="dataset_changed")
                message = {
                    409: "이미 있는 등록본 ID입니다. 다시 등록해 주세요." if code == "dataset_exists"
                    else "로컬 수신 서버가 요청을 거절했습니다. 현재 등록본을 다시 확인해 주세요.",
                    501: "CKKS 암호화 코어가 아직 연결되지 않았습니다.",
                }.get(res.status_code, "로컬 수신 서버가 요청을 거절했습니다.")
                if res.status_code >= 500 and res.status_code != 501:
                    raise AmbiguousDelivery(message, status=res.status_code)
                raise InputError(message, status=res.status_code)
            result = res.json()
            if not isinstance(result, dict):
                raise ValueError("Expected response object")
            if path == "/v1/datasets":
                active = result.get("active")
                if not isinstance(active, dict) or any(active.get(key) != payload[key] for key in
                        ("dataset_id", "transformer_hash", "scheme", "records", "dimension")):
                    raise ValueError("Unexpected registration acknowledgement")
            return result
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise NotSent("로컬 수신 서버에 연결하지 못했습니다. Medical-Search-API의 실행과 연결 설정을 확인해 주세요.", status=502) from None
        except (httpx.HTTPError, ValueError):
            raise AmbiguousDelivery("수신 서버의 응답을 확인하지 못했습니다. ‘연결 다시 확인’으로 등록 상태를 확인해 주세요.", status=502) from None

    def current_active():
        """수신 서버의 활성 등록본. 연결하지 못하면 None."""
        try:
            return remote("GET", "/v1/meta").get("active")
        except InputError:
            return None

    loaded = {}  # (등록본 ID, 해시) → 전처리기. 활성 등록본 하나만 둔다.
    loaded_lock = threading.Lock()

    def load_transformer(dataset_id, expected_hash):
        """해시를 확인한 바이트에서 그대로 전처리기를 읽는다 (확인과 읽기 사이에 파일이 바뀌어도 안전)."""
        try:
            if str(uuid.UUID(str(dataset_id))) != dataset_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise InputError("등록본 ID가 올바르지 않습니다.") from None
        try:
            raw = (data_dir / dataset_id / "preprocessor.joblib").read_bytes()
        except OSError:
            raise InputError("이 등록본의 기관 전처리기가 없습니다. CSV를 다시 등록해 주세요.", status=409) from None
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected_hash:
            raise InputError("전처리기 버전이 등록본과 다릅니다. 다시 등록해 주세요.", status=409)
        with loaded_lock:
            transformer = loaded.get((dataset_id, digest))
            if transformer is None:
                try:
                    transformer = joblib.load(io.BytesIO(raw))
                    encoder = transformer.named_transformers_["categorical"].named_steps["encode"]
                    if len(encoder.categories_) != len(pipeline.CATEGORICAL_COLUMNS):
                        raise ValueError("Unexpected transformer")
                except Exception:
                    raise InputError("이 등록본의 기관 전처리기를 읽지 못했습니다. CSV를 다시 등록해 주세요.", status=409) from None
                loaded.clear()
                loaded[(dataset_id, digest)] = transformer
        return transformer, digest

    def known_categories(transformer):
        encoder = transformer.named_transformers_["categorical"].named_steps["encode"]
        return {column: [str(value) for value in values]
                for column, values in zip(pipeline.CATEGORICAL_COLUMNS, encoder.categories_)}

    def active_status():
        try:
            active = remote("GET", "/v1/meta").get("active")
        except InputError:
            return {"active": None, "server_connected": False, "encryption_connected": False,
                    "query_ready": False, "categories": None}
        categories = None
        if active:
            try:
                categories = known_categories(load_transformer(active["dataset_id"], active["transformer_hash"])[0])
            except InputError:
                categories = None
        # 범주 목록은 검색 화면이 등록본에 있는 값만 고르게 하는 데 쓴다. 서버 쪽 검사는 그대로 한다.
        return {"active": active, "server_connected": True, "encryption_connected": False,
                "query_ready": categories is not None, "categories": categories}

    # 두 화면: 여러 번 쓰는 등록본 검색(/)과 데이터셋마다 한 번 쓰는 데이터셋 등록(/register)
    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "datasets" / "index.html")

    @app.get("/register")
    def register_page():
        return FileResponse(ROOT / "static" / "datasets" / "register.html")

    @app.get("/static/{filename}")
    def asset(filename: str):
        if filename not in {"app.js", "style.css"}:
            raise InputError("파일을 찾을 수 없습니다.", status=404)
        return FileResponse(ROOT / "static" / "datasets" / filename)

    @app.get("/health")
    def health():
        return {"ok": True, "mode": "local_plaintext"}

    @app.get("/api/schema")
    def schema():
        return {"fields": FIELDS, "groups": GROUPS, "max_bytes": MAX_CSV_BYTES, "max_rows": MAX_ROWS,
                "mode": "local_plaintext", "encryption_connected": False}

    @app.get("/api/sample")
    def sample():
        return FileResponse(ROOT / "data" / "dataset-sample.csv", media_type="text/csv", filename="veil-synthetic-sample.csv")

    @app.get("/api/status")
    def status():
        return active_status()

    @app.post("/api/inspect")
    async def inspect(request: Request):
        data = await body(request)
        columns, rows = parse_csv(data.get("csv_text"))
        by_lower = {column.lower(): column for column in columns}
        # 원본 행은 돌려주지 않는다. 화면에 필요한 것은 열 이름과 행 수뿐이다.
        return {"columns": columns, "row_count": len(rows),
                "mapping": {key: by_lower.get(key, "") for key in KEYS}}

    @app.post("/api/preview")
    async def preview(request: Request):
        data = await body(request)
        columns, rows = parse_csv(data.get("csv_text"))
        name, mapping = data.get("name"), data.get("mapping")
        # 이름 오류는 field "name" 으로 알려 화면이 1단계의 이름 칸을 가리키게 한다.
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise InputError("데이터셋 이름을 확인해 주세요.",
                             issues=[{"row": None, "field": "name", "message": "1~80자로 입력해 주세요."}])
        # 수신 서버와 같은 규칙. 여기서 거르지 않으면 검증은 통과하고 등록 단계에서야 거절된다.
        if any(ord(char) < 32 for char in name.strip()):
            raise InputError("데이터셋 이름을 확인해 주세요.", issues=[
                {"row": None, "field": "name", "message": "탭이나 줄바꿈 같은 제어 문자는 쓸 수 없습니다."}])
        if not isinstance(mapping, dict) or set(mapping) != set(KEYS):
            raise InputError("의료 속성 13개에 대응하는 열을 모두 연결해 주세요.")
        if any(not isinstance(mapping[key], str) or mapping[key] not in columns for key in KEYS):
            raise InputError("의료 속성 13개에 대응하는 열을 모두 연결해 주세요.")
        if len(set(mapping.values())) != len(KEYS):
            raise InputError("하나의 원본 열을 두 속성에 연결할 수 없습니다.")
        # Outcome labels must not accidentally become medical features. 어느 속성에 연결했는지 알려 준다.
        blocked = [key for key in KEYS if mapping[key].lower() in BLOCKED_COLUMNS]
        if blocked:
            raise InputError("정답 레이블·식별자 열은 의료 속성에 연결할 수 없습니다.", issues=[
                {"row": None, "field": key, "message": f"{mapping[key]} 열은 연결할 수 없습니다."} for key in blocked])
        normalized, issues = [], []
        for number, row in rows:
            clean, errors = normalize_row({key: row[source] for key, source in mapping.items()}, number)
            normalized.append(clean)
            issues.extend(errors)
        if issues:
            shown = " 처음 50개만 표시합니다." if len(issues) > 50 else ""
            raise InputError(f"입력 오류 {len(issues):,}개를 고친 뒤 다시 검증해 주세요.{shown}", issues=issues[:50])
        missing = sum(value is None for row in normalized for value in row.values())
        token_id = secrets.token_urlsafe(24)
        with preview_lock:
            now = time.monotonic()
            for key in list(previews):
                if now - previews[key]["created"] > PREVIEW_SECONDS:
                    del previews[key]
            while len(previews) >= MAX_PREVIEWS:
                previews.popitem(last=False)
            previews[token_id] = {"created": now, "name": name.strip(), "rows": normalized, "registered": None}
        ignored = [column for column in columns if column not in mapping.values()]
        # sample_lines: 미리보기 행의 스프레드시트 행 번호(머리글이 1행). 오류 목록의 "N행"과 같은 기준이다.
        return {"preview_id": token_id, "records": len(normalized), "missing_cells": missing,
                "ignored_columns": ignored, "sample": normalized[:5],
                "sample_lines": [number for number, _ in rows[:5]],
                "notes": ["등록용 전체 행을 사용합니다. 평가 질의로 분할하지 않습니다.",
                          "숫자 결측은 중앙값으로 대체하고, 범주 결측은 별도 범주로 인코딩합니다.",
                          "현재 테스트에서는 정규화된 평문 벡터가 로컬 수신 서버로 전송됩니다."]}

    def enroll(data):
        if data.get("scheme") != "plaintext":
            raise InputError("CKKS 암호화 코어가 아직 연결되지 않았습니다. 평문 테스트만 가능합니다.", status=501)
        preview_id = data.get("preview_id")
        if not isinstance(preview_id, str):
            raise InputError("먼저 CSV 검증을 완료해 주세요.")
        with enrollment_lock:
            with preview_lock:
                entry = previews.get(preview_id)
                if entry is None or time.monotonic() - entry["created"] > PREVIEW_SECONDS:
                    raise InputError("검증 결과가 만료되었습니다. 2단계에서 전체 행 검증을 다시 눌러 주세요.", status=410)
            if entry["registered"]:
                active = remote("GET", "/v1/meta").get("active")
                if not active or active["dataset_id"] != entry["registered"]["active"]["dataset_id"]:
                    raise InputError("다른 등록본이 활성화되었습니다. CSV를 다시 검증해 주세요.", status=409)
                return entry["registered"]
            frame = _frame(entry["rows"])
            transformer = pipeline.make_preprocessor()
            encoded = np.asarray(transformer.fit_transform(frame), dtype=np.float64)
            dimension = 1 << (encoded.shape[1] - 1).bit_length()
            vectors = pipeline.pad_and_normalize(encoded, dimension)
            dataset_id = str(uuid.uuid4())
            snapshot = data_dir / dataset_id
            snapshot.mkdir(parents=True, exist_ok=False)
            # 요청이 미전송·명시적으로 거절됐을 때만 정리한다. 응답이 불확실하면 보관한다.
            # 메타 조회가 이전 DB여도 원래 POST가 나중에 완료될 수 있다.
            keep = False
            try:
                transformer_path = snapshot / "preprocessor.joblib"
                joblib.dump(transformer, transformer_path)
                digest = hashlib.sha256(transformer_path.read_bytes()).hexdigest()
                ids = [f"R{i:06d}" for i in range(1, len(vectors) + 1)]
                metadata = {"dataset_id": dataset_id, "name": entry["name"], "scheme": "plaintext",
                            "transformer_hash": digest, "records": len(vectors), "dimension": dimension,
                            "numeric_columns": pipeline.NUMERIC_COLUMNS,
                            "categorical_columns": pipeline.CATEGORICAL_COLUMNS,
                            "encoded_dimension": int(encoded.shape[1])}
                (snapshot / "manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
                # This contains row positions only. No CSV or clinical attributes are persisted.
                (snapshot / "row-map.json").write_text(json.dumps({record_id: i + 1 for i, record_id in enumerate(ids)}), encoding="utf-8")
                payload = {key: metadata[key] for key in ("dataset_id", "name", "scheme", "transformer_hash", "records", "dimension")}
                payload.update(ids=ids, vectors=vectors.tolist())
                try:
                    active = remote("POST", "/v1/datasets", payload)["active"]
                except NotSent:
                    raise
                except InputError as exc:
                    # A timeout/invalid response may precede a delayed commit. An older
                    # meta snapshot does not prove this request will never activate.
                    keep = isinstance(exc, AmbiguousDelivery)
                    current = current_active()
                    if not current or current["dataset_id"] != dataset_id:
                        raise
                    active = current
                keep = True
            finally:
                if not keep:
                    shutil.rmtree(snapshot, ignore_errors=True)
            # say: 처리 과정 패널의 큰 줄 (의료진 화면 시안 C처럼 한 문장). label · detail 은 작은 기술 줄.
            rows = f"{len(vectors):,}행"
            result = {"active": active, "stages": [
                {"label": "CSV 검증", "status": "done", "say": f"{rows}을 모두 검증했어요",
                 "detail": f"{len(KEYS)}개 의료 속성"},
                {"label": "일괄 인코딩", "status": "done", "say": f"{rows}을 {dimension}차원 벡터로 한 번에 바꿨어요",
                 "detail": "L2 정규화"},
                {"label": "CKKS 암호화", "status": "not_connected", "say": "CKKS 암호화는 아직 연결 전이라 건너뛰었어요",
                 "detail": "평문 테스트로 등록"},
                {"label": "수신 서버 등록", "status": "done", "say": "로컬 수신 서버에 새 등록본으로 올렸어요",
                 "detail": "새 등록본 활성화 · 이전 등록본 보관"},
            ]}
            with preview_lock:
                entry["registered"] = result
            return result

    @app.post("/api/register")
    async def register(request: Request):
        data = await body(request)
        try:
            return await run_in_threadpool(enroll, data)
        except (OSError, ValueError, RuntimeError):
            raise InputError("인코딩 또는 저장에 실패했습니다. CSV와 로컬 폴더 상태를 확인해 주세요.", status=500) from None

    def query(data):
        dataset_id = data.get("dataset_id")
        try:
            if str(uuid.UUID(str(dataset_id))) != dataset_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise InputError("등록본 ID가 올바르지 않습니다.") from None
        active = remote("GET", "/v1/meta").get("active")
        if not active or active["dataset_id"] != dataset_id:
            raise InputError("현재 등록본이 바뀌었습니다. 현재 등록본을 다시 확인해 주세요.", status=409, code="dataset_changed")
        values = data.get("values")
        if not isinstance(values, dict) or set(values) != set(KEYS):
            raise InputError(f"질의에 의료 속성 {len(KEYS)}개가 필요합니다.")
        normalized, issues = normalize_row(values, None)  # 질의는 한 건이라 행 번호가 없다
        if issues:
            raise InputError("검색 입력값을 확인해 주세요.", issues=issues)
        top_k = data.get("top_k", 5)
        if type(top_k) is not int or top_k not in (3, 5, 10):
            raise InputError("결과 개수는 3, 5, 10 중에서 선택해 주세요.")
        transformer, digest = load_transformer(dataset_id, active["transformer_hash"])
        known = known_categories(transformer)
        # 모르는 범주는 원-핫 인코더가 조용히 0으로 바꾸므로 여기서 먼저 거절한다.
        unknown = [column for column in pipeline.CATEGORICAL_COLUMNS
                   if (MISSING if normalized[column] is None else normalized[column]) not in known[column]]
        if unknown:
            raise InputError("등록본에 없는 범주로는 검색할 수 없습니다.", issues=[
                {"row": None, "field": column,
                 "message": "이 등록본에는 이 항목의 결측값이 없어 비워 둘 수 없습니다." if normalized[column] is None
                 else "이 등록본에 없는 값입니다. 다른 값을 선택해 주세요."}
                for column in unknown])
        try:
            encoded = transformer.transform(_frame([normalized]))
            vector = pipeline.pad_and_normalize(np.asarray(encoded, dtype=np.float64), active["dimension"])[0].tolist()
        except (ValueError, RuntimeError):
            raise InputError("질의를 벡터로 바꾸지 못했습니다. CSV를 다시 등록해 주세요.", status=409) from None
        return remote("POST", "/v1/search", {"dataset_id": dataset_id, "transformer_hash": digest,
                                            "scheme": "plaintext", "vector": vector, "top_k": top_k})

    @app.post("/api/search")
    async def search(request: Request):
        return await run_in_threadpool(query, await body(request))

    return app

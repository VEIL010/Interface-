"""Blind-Match 의료 유사 기록 검색 — UI 시안 서버 (FastAPI + Jinja2).

실행 (Blind-Match 폴더에서):
    webui/.venv/Scripts/python -m uvicorn app.main:app --app-dir webui --reload --port 8123

화면 흐름
    GET  /                                    의료진 검색 화면 (시안 A)
    GET  /drafts                              보관된 시안 목록
    GET  /drafts/{key}                         A·B 질의 입력 또는 C·D 보관 시안
    POST /drafts/{key}/search                  기존 데모 검색 결과 HTML 조각
    POST /drafts/a/heart-search                13개 속성 질의 → Medical-Search-API 검색 결과 HTML 조각
    POST /drafts/{key}/records/{id}/access     상세 기록 HTML 조각 (_record.html)

같은 기능의 JSON API 는 /api/* 에 있다 (/api/docs 참고).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound

from . import backend, heart_fields, heart_query

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_DIR = BASE_DIR / "templates"

app = FastAPI(title="Blind-Match UI 시안", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATE_DIR)

DRAFTS = {
    "a": {
        "key": "a",
        "letter": "A",
        "name": "진료 워크스테이션",
        "tagline": "입력과 결과를 한 화면에 나란히 두는 2단 구성",
    },
    "b": {
        "key": "b",
        "letter": "B",
        "name": "암호 파이프라인",
        "tagline": "클라이언트 → 서버 → 클라이언트 흐름을 그대로 보여주는 3레인",
    },
    "c": {
        "key": "c",
        "letter": "C",
        "name": "단계형 가이드",
        "tagline": "한 번에 한 단계씩 따라가는 1단 구성",
    },
    "d": {
        "key": "d",
        "letter": "D",
        "name": "벤치마크 콘솔",
        "tagline": "방식별 결과와 측정값을 비교하는 대시보드",
    },
}


def asset(path: str) -> str:
    """정적 파일 URL. 수정 시각을 붙여 브라우저 캐시를 무효화한다."""
    file = STATIC_DIR / path
    version = int(file.stat().st_mtime) if file.exists() else 0
    return f"/static/{path}?v={version}"


templates.env.globals["asset"] = asset


def _draft_ready(key: str) -> bool:
    return (TEMPLATE_DIR / "drafts" / key / "page.html").exists()


def _run_search(data: dict) -> tuple[dict, dict | None, dict | None, int]:
    """입력 검증 후 검색. (form, result, error, HTTP 상태)를 돌려준다."""
    form, field_errors = backend.parse_form(data)
    if field_errors:
        return form, None, {"message": "입력값을 확인해 주세요.", "fields": field_errors}, 422
    try:
        result = backend.search(form)
    except backend.SearchError as exc:
        return form, None, {"message": str(exc), "fields": {}}, exc.status
    return form, result, None, 200


def _with_links(result: dict | None, access_prefix: str) -> dict | None:
    """결과 행마다 상세 기록 요청 URL과 DOM id 를 붙인다."""
    if result is not None:
        for item in result["results"]:
            item["access_url"] = f"{access_prefix}/{item['record_id']}/access"
            item["dom_id"] = f"record-{item['record_id']}"
    return result


def _draft_context(key: str, form=None, result=None, error=None) -> dict:
    return {
        "draft": DRAFTS[key],
        "drafts": list(DRAFTS.values()),
        "meta": backend.meta(),
        "form": form if form is not None else backend.default_form(),
        "result": result,
        "error": error,
        "urls": {
            "home": "/",
            "archive": "/drafts",
            "presentation": "/drafts/b?state=empty",
            "page": f"/drafts/{key}",
            "empty": f"/drafts/{key}?state=empty",
            "search": f"/drafts/{key}/search",
            "heart_search": "/drafts/a/heart-search",
        },
    }


async def _read_json(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def _mock_delay(data: dict) -> None:
    """로딩 상태를 눈으로 확인하기 위한 개발용 지연 (요청의 mock_delay_ms, 최대 5초)."""
    try:
        ms = int(data.get("mock_delay_ms") or 0)
    except (TypeError, ValueError):
        ms = 0
    if ms > 0:
        await asyncio.sleep(min(ms, 5000) / 1000)


def _not_found(message: str) -> HTMLResponse:
    return HTMLResponse(f'<p role="alert">{message}</p>', status_code=404)


# ───────────────────────── 화면 ─────────────────────────


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return draft_page(request, "a", state="empty")


@app.get("/drafts", response_class=HTMLResponse)
def draft_archive(request: Request):
    drafts = [{**DRAFTS[key], "ready": _draft_ready(key)} for key in ("c", "d")]
    return templates.TemplateResponse(
        request=request, name="index.html", context={"drafts": drafts, "meta": backend.meta()}
    )


@app.get("/drafts/{key}", response_class=HTMLResponse)
def draft_page(request: Request, key: str, state: str | None = None):
    if key not in DRAFTS:
        return _not_found("없는 시안입니다.")
    if not _draft_ready(key):
        return _not_found(f"시안 {DRAFTS[key]['letter']} 는 아직 준비 중입니다.")
    if key in ("a", "b") and state != "legacy":
        form = {**heart_fields.form_values(example=key == "b"), "top_k": heart_query.DEFAULT_TOP_K}
        context = _heart_context(key, form)
    elif state == "empty":
        context = _draft_context(key)
    else:
        form, result, error, _ = _run_search(backend.default_form())
        context = _draft_context(key, form, _with_links(result, f"/drafts/{key}/records"), error)
        context["initial_example"] = True
    return templates.TemplateResponse(
        request=request, name=f"drafts/{key}/page.html", context=context
    )


def _heart_context(key: str, form: dict, result=None, error=None) -> dict:
    context = _draft_context(key, form, result, error)
    context["heart_fields"] = heart_fields.FIELDS
    context["heart_groups"] = heart_fields.groups()
    # 검색은 의료진 화면(A)에서만 켠다. 발표 모드(B)는 목표 흐름을 보여 주는 화면이다.
    context["heart_search"] = key == "a" and heart_query.enabled()
    return context


@app.post("/drafts/a/heart-search", response_class=HTMLResponse)
async def heart_search(request: Request):
    if not heart_query.enabled():
        return _not_found("검색 API가 연결되지 않았습니다.")
    form, values, top_k, field_errors = heart_query.parse(await _read_json(request))
    result = error = None
    status = 200
    if field_errors:
        error, status = {"message": "입력값을 확인해 주세요.", "fields": field_errors}, 422
    else:
        try:
            vector = await run_in_threadpool(heart_query.vectorize, values)
            found = await run_in_threadpool(heart_query.search, vector, top_k)
            result = heart_query.present(form, values, top_k, found)
        except heart_query.SearchUnavailable as exc:
            error, status = {"message": str(exc), "fields": {}}, 502
        except RuntimeError:
            error, status = {"message": "입력값을 질의 벡터로 바꾸지 못했습니다.", "fields": {}}, 500
    context = _heart_context("a", {**form, "top_k": top_k}, result, error)
    return templates.TemplateResponse(
        request=request, name="drafts/a/_results.html", context=context, status_code=status
    )


@app.post("/drafts/{key}/search", response_class=HTMLResponse)
async def draft_search(request: Request, key: str):
    if key not in DRAFTS or not _draft_ready(key):
        return _not_found("없는 시안입니다.")
    data = await _read_json(request)
    await _mock_delay(data)
    form, result, error, status = _run_search(data)
    context = _draft_context(key, form, _with_links(result, f"/drafts/{key}/records"), error)
    return templates.TemplateResponse(
        request=request, name=f"drafts/{key}/_results.html", context=context, status_code=status
    )


@app.post("/drafts/{key}/records/{record_id}/access", response_class=HTMLResponse)
async def draft_record_access(request: Request, key: str, record_id: str):
    if key not in DRAFTS or not _draft_ready(key):
        return _not_found("없는 시안입니다.")
    await _mock_delay(await _read_json(request))
    try:
        access = backend.request_access(record_id)
        status = 200 if access["granted"] else 403
    except KeyError:
        access = backend.unknown_record(record_id)
        status = 404
    context = {**_draft_context(key), "access": access}
    return templates.TemplateResponse(
        request=request, name=f"drafts/{key}/_record.html", context=context, status_code=status
    )


# ───────────────────────── JSON API ─────────────────────────


@app.get("/api/meta")
def api_meta():
    return backend.meta()


@app.post("/api/search")
async def api_search(request: Request):
    data = await _read_json(request)
    await _mock_delay(data)
    form, result, error, status = _run_search(data)
    if error is not None:
        return JSONResponse({"error": error, "form": form}, status_code=status)
    return _with_links(result, "/api/records")


@app.post("/api/records/{record_id}/access")
def api_record_access(record_id: str):
    try:
        access = backend.request_access(record_id)
    except KeyError:
        return JSONResponse(backend.unknown_record(record_id), status_code=404)
    return JSONResponse(access, status_code=200 if access["granted"] else 403)


@app.get("/api/access-log")
def api_access_log():
    return {"requester": backend.REQUESTER, "entries": backend.ACCESS_LOG}


@app.get("/healthz")
def healthz():
    return {"ok": True, "backend": backend.BACKEND_INFO["mode"], "records": len(backend.RECORDS)}


# 템플릿 안의 include 가 빠졌을 때 원인을 바로 알 수 있게 한다
@app.exception_handler(TemplateNotFound)
async def template_not_found(request: Request, exc: TemplateNotFound):
    return HTMLResponse(f'<p role="alert">템플릿이 없습니다: {exc.name}</p>', status_code=500)

"""시안 스모크 테스트 — 서버를 띄우지 않고 TestClient 로 확인한다.

사용 (Blind-Match 폴더에서):
    webui/.venv/Scripts/python webui/tests/smoke.py          # 준비된 시안 전부
    webui/.venv/Scripts/python webui/tests/smoke.py a c      # 지정한 시안만

실패가 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations

import json
import os
import re
import sys
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
# Starlette 1.x 가 TestClient 용 httpx 를 httpx2 로 바꾸라고 알리는 경고 — 동작에는 영향 없음
warnings.filterwarnings("ignore", message=".*starlette.testclient.*")

from fastapi.testclient import TestClient  # noqa: E402

from app import backend, heart_fields, heart_query  # noqa: E402
from app.main import DRAFTS, STATIC_DIR, TEMPLATE_DIR, app  # noqa: E402

EXAMPLE = backend.default_form()
EXPECTED_TOP3 = [("P023", "0.971"), ("P104", "0.923"), ("P056", "0.881")]

client = TestClient(app)
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("  ok    " if condition else "  FAIL  ") + message)
    if not condition:
        failures.append(message)


def check_api() -> None:
    print("[공통] 서버와 JSON API")
    home = client.get("/")
    check(home.status_code == 200 and 'name="trestbps"' in home.text, "GET / → 의료진 질의 입력 화면")
    check("P023" not in home.text and 'href="/drafts/b"' in home.text, "기본 화면은 검색 전 상태이며 발표 모드에 연결")
    archive = client.get("/drafts")
    check(archive.status_code == 200 and all(f'href="/drafts/{key}"' in archive.text for key in DRAFTS), "목록에서 A·B·C·D 열기")
    check(client.get("/healthz").json().get("ok") is True, "GET /healthz")

    meta = client.get("/api/meta").json()
    check([f["key"] for f in meta["features"]] == ["age", "resting_bp", "cholesterol", "max_hr"], "meta.features 4개")

    res = client.post("/api/search", json=EXAMPLE)
    body = res.json()
    got = [(r["record_id"], r["score_text"]) for r in body.get("results", [])]
    check(res.status_code == 200 and got == EXPECTED_TOP3, f"예시 질의 Top-3 = 제안서 예시 ({got})")
    check([s["status"] for s in body["pipeline"]] == ["done", "not_connected", "not_connected", "substituted", "not_connected", "done"], "pipeline 상태 (mock)")

    check(client.post("/api/search", json={**EXAMPLE, "age": "abc"}).status_code == 422, "잘못된 입력 → 422")
    check(client.post("/api/search", json={**EXAMPLE, "method": "he_c3"}).status_code == 501, "미연결 방식 → 501")
    check(client.post("/api/records/P023/access").status_code == 200, "P023 열람 허용 → 200")
    check(client.post("/api/records/P056/access").status_code == 403, "P056 열람 제한 → 403")
    check(client.post("/api/records/P999/access").status_code == 404, "없는 기록 → 404")


def check_draft(key: str) -> None:
    letter = DRAFTS[key]["letter"]
    print(f"[시안 {letter}] {DRAFTS[key]['name']}")
    base = TEMPLATE_DIR / "drafts" / key
    for name in ("page.html", "_results.html", "_record.html"):
        check((base / name).exists(), f"templates/drafts/{key}/{name} 존재")
    check((STATIC_DIR / "drafts" / key / "style.css").exists(), f"static/drafts/{key}/style.css 존재")
    if not (base / "page.html").exists():
        return

    legacy_query = "?state=legacy" if key in ("a", "b") else ""
    page = client.get(f"/drafts/{key}{legacy_query}")
    html = page.text
    check(page.status_code == 200, "전체 페이지 → 200")
    check('lang="ko"' in html, 'html lang="ko"')
    check('name="viewport"' in html, "viewport meta")
    check(f"/static/drafts/{key}/style.css?v=" in html, "style.css 를 asset() 으로 연결")
    check("/static/shared/bm.js" in html, "공용 bm.js 연결")
    check("data-bm-search" in html and "data-bm-target" in html, "검색 폼 data-bm-search / data-bm-target")
    check(f'action="/drafts/{key}/search"' in html, "검색 폼 action = urls.search")
    check("{{" not in html and "{%" not in html, "렌더링되지 않은 템플릿 문법 없음")
    for f in backend.FEATURES:
        check(f'name="{f.key}"' in html, f"입력 필드 name={f.key}")
        check(f.label in html, f"라벨 '{f.label}'")
    check('name="top_k"' in html, "Top-K 선택 name=top_k")
    if key in ("c", "d"):
        check(backend.BACKEND_INFO["label"] in html, "합성 데모 백엔드 표시")
    for record_id, score in EXPECTED_TOP3:
        check(record_id in html and score in html, f"초기 결과에 {record_id} {score}")
    check("진단" in html, "유사도 ≠ 진단 고지 문구")
    access_url = f"/drafts/{key}/records/P023/access"
    tag = re.search(r'<[^>]*data-bm-access="%s"[^>]*>' % re.escape(access_url), html)
    check(tag is not None, "상세 기록 요청 버튼 (data-bm-access)")
    target = re.search(r'data-bm-target="#([^"]+)"', tag.group(0)) if tag else None
    check(bool(target) and f'id="{target.group(1)}"' in html, "상세 기록 버튼의 data-bm-target 영역이 페이지에 있음")
    check(len(re.findall(r'data-bm-access="', html)) == 3, "결과 행마다 상세 기록 요청 버튼 (3개)")

    empty = client.get(f"/drafts/{key}?state=empty")
    check(empty.status_code == 200 and "P023" not in empty.text, "빈 상태 페이지 (결과 없음)")
    if key in ("c", "d"):
        check("data-bm-search" in empty.text, "빈 상태에도 검색 폼")

    frag = client.post(f"/drafts/{key}/search", json=EXAMPLE)
    check(frag.status_code == 200 and "P023" in frag.text and "0.971" in frag.text, "검색 조각 → 200, 결과 포함")
    check("<html" not in frag.text.lower() and "<body" not in frag.text.lower(), "검색 조각은 문서 전체가 아님")

    five = backend.search({**EXAMPLE, "top_k": 5})["results"]
    frag5 = client.post(f"/drafts/{key}/search", json={**EXAMPLE, "top_k": "5"})
    check(frag5.status_code == 200 and all(r["record_id"] in frag5.text for r in five), "Top-5 검색 조각에 5건")

    bad = client.post(f"/drafts/{key}/search", json={**EXAMPLE, "age": "abc"})
    check(bad.status_code == 422 and "입력값을 확인" in bad.text, "잘못된 입력 → 422, 오류 메시지 표시")
    check("숫자를 입력하세요" in bad.text, "필드별 오류 메시지 표시")
    check('data-bm-invalid="age"' in bad.text, '오류 조각에 data-bm-invalid="age"')
    check('role="alert"' in bad.text, '오류 조각에 role="alert"')

    unavailable = client.post(f"/drafts/{key}/search", json={**EXAMPLE, "method": "he_c3"})
    check(unavailable.status_code == 501 and "연결되지 않았습니다" in unavailable.text, "미연결 방식 → 501, 안내 표시")

    record = backend.BY_ID["P023"]
    ok = client.post(f"/drafts/{key}/records/P023/access", json={})
    check(ok.status_code == 200 and all(str(v) in ok.text for v in record.values.values()), "P023 상세 기록 조각 (값 4개 표시)")
    check("<html" not in ok.text.lower(), "상세 기록 조각은 문서 전체가 아님")
    denied = client.post(f"/drafts/{key}/records/P056/access", json={})
    check(denied.status_code == 403 and "권한" in denied.text, "P056 → 403, 권한 없음 표시")
    missing = client.post(f"/drafts/{key}/records/P999/access", json={})
    check(missing.status_code == 404 and "존재하지 않는" in missing.text, "없는 기록 → 404 표시")


def check_heart_draft(key: str) -> None:
    print(f"[시안 {key.upper()}] Heart Disease 질의 입력")
    page = client.get(f"/drafts/{key}")
    check(page.status_code == 200 and "검색 API 연결 대기" in page.text, "검색 API 연결 대기 상태 표시")
    check("<form" not in page.text and "data-bm-search" not in page.text, "API 연결 전 질의 제출 경로 없음")
    check_heart_fields(page.text)


def check_heart_fields(html: str) -> None:
    check(
        all(f'name="{field["key"]}"' in html for field in heart_fields.FIELDS)
        and len(heart_fields.FIELDS) == 13 and 'name="num"' not in html,
        "결과 레이블을 제외한 13개 파이프라인 입력",
    )


def write_preprocessor_fixture(folder: Path) -> None:
    """공개 데이터 다운로드 없이 운영 전처리기와 같은 입력 계약의 모델을 만든다."""
    import joblib
    import pandas as pd
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import Pipeline, make_pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric = [field["key"] for field in heart_fields.FIELDS if not field["options"]]
    categorical = [field["key"] for field in heart_fields.FIELDS if field["options"]]
    examples = heart_fields.form_values(example=True)
    columns = {key: [float(examples[key]) + i for i in range(4)] for key in numeric}
    for field in heart_fields.FIELDS:
        if field["options"]:
            values = [value for value, _ in field["options"]]
            if field["key"] == "thal":
                values = [str(float(value)) for value in values] + [heart_query.MISSING]
            columns[field["key"]] = [values[i % len(values)] for i in range(4)]
    transformer = ColumnTransformer([
        ("numeric", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numeric),
        ("categorical", Pipeline([("encode", OneHotEncoder(sparse_output=False))]), categorical),
    ]).fit(pd.DataFrame(columns))
    joblib.dump(transformer, folder / "preprocessor.joblib")
    (folder / "metadata.json").write_text(json.dumps({
        "numeric_columns": numeric, "categorical_columns": categorical, "padded_dimension": 32,
    }), encoding="utf-8")


def check_heart_search(key: str, pipeline_dir: Path) -> None:
    """검색 API 가 연결된 A·C 화면을 로컬 전처리기와 가짜 API로 확인한다."""
    print(f"[시안 {key.upper()}] Heart Disease 검색 (검색 API 연결)")
    import httpx

    requests: list[httpx.Request] = []
    seen: list[dict] = []

    def fake_api(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        seen.append(body)
        hits = [{"rank": i, "record_id": f"H{i:05d}", "score": 1 - i / 10} for i in range(1, body["top_k"] + 1)]
        return httpx.Response(200, json={"encrypted": False, "searched": 283, "results": hits})

    path = f"/drafts/{key}/heart-search"
    example = {**heart_fields.form_values(example=True), "top_k": "5"}
    with patch.dict(os.environ, {
        "VEIL_SEARCH_API_URL": "http://search.test",
        "VEIL_SEARCH_API_KEY": "test-key",
        "VEIL_PIPELINE_DIR": str(pipeline_dir),
    }), patch.object(heart_query, "transport", httpx.MockTransport(fake_api)):
        page = client.get("/" if key == "a" else "/drafts/c")
        check(page.status_code == 200 and f'action="{path}"' in page.text, "검색 폼이 켜짐")
        check("검색 API 연결 대기" not in page.text, "연결 대기 문구가 남지 않음")
        check('data-bm-target="#results"' in page.text and "/static/shared/bm.js" in page.text, "공용 검색 스크립트와 결과 영역 연결")
        check(re.search(r'class="bm-devbar__status"[^>]*>평문 검색 연결</span>', page.text) is not None
              and backend.BACKEND_INFO["label"] not in page.text, "개발용 상태 막대에 평문 검색 연결 표시")
        check_heart_fields(page.text)
        check("data-bm-search" not in client.get("/drafts/b").text, "발표 모드는 검색을 켜지 않음")

        if key == "c":
            check("P023" not in page.text and 'href="/drafts/c?state=empty"' in page.text, "C 실검색 화면은 검색 전 상태에서 열림")
            legacy = client.get("/drafts/c?state=legacy")
            check(legacy.status_code == 200 and 'action="/drafts/c/search"' in legacy.text
                  and "P023" in legacy.text and backend.BACKEND_INFO["label"] in legacy.text,
                  "API 연결 후에도 C 합성 데모를 열 수 있음")
            legacy_empty = client.get("/drafts/c?state=legacy-empty")
            check('href="/drafts/c?state=legacy-empty"' in legacy.text and legacy_empty.status_code == 200
                  and 'action="/drafts/c/search"' in legacy_empty.text and "P023" not in legacy_empty.text,
                  "C 합성 데모 초기화는 데모 입력 경로를 유지함")

        frag = client.post(path, json=example)
        check(frag.status_code == 200 and "H00001" in frag.text and "0.900" in frag.text, "검색 조각 → 200, 결과 표시")
        check("<html" not in frag.text.lower() and "<body" not in frag.text.lower(), "실검색 응답은 HTML 조각")
        if key == "c":
            check(all(f"H{i:05d}" in frag.text for i in range(1, 6)) and "H00006" not in frag.text,
                  "C Top-5 결과를 모두 표시")
            check("CKKS" in frag.text and "상세 의료 기록 조회" in frag.text
                  and "data-bm-access" not in frag.text, "C 실검색 결과에 암호화 미연결 안내가 있고 상세 기록 버튼은 없음")
        sent = seen[-1] if seen else {}
        vector = sent.get("vector", [])
        check(bool(requests) and requests[0].method == "POST" and requests[0].url.path == "/v1/search",
              "Medical-Search-API 검색 경로 호출")
        check(bool(requests) and requests[0].headers.get("X-API-Key") == "test-key", "설정된 검색 API 키 전달")
        check(sent.get("scheme") == "plaintext" and sent.get("top_k") == 5, "검색 서버에 scheme·top_k 전달")
        check(len(vector) == 32 and abs(sum(x * x for x in vector) - 1) < 1e-9, "32차원 L2 정규화 벡터 전달")
        check(not (set(sent) - {"scheme", "vector", "top_k"}), "원본 의료 속성은 검색 서버로 보내지 않음")

        thal = {**example, "thal": "7"}
        changed = client.post(path, json=thal)
        check(changed.status_code == 200 and len(seen) == 2 and seen[-1]["vector"] != vector,
              "thal 값이 벡터에 반영됨 (전처리기 범주 표기와 일치)")

        optional = client.post(path, json={**example, "ca": "", "thal": ""})
        check(optional.status_code == 200, "선택 입력(ca·thal)을 비워도 검색")

        request_count = len(seen)
        bad = client.post(path, json={**example, "age": "abc", "sex": "9", "chol": ""})
        check(bad.status_code == 422 and 'data-bm-invalid="age,chol,sex"' in bad.text, "잘못된 입력 → 422, 필드 표시")
        check(len(seen) == request_count, "유효하지 않은 입력은 검색 API에 전송하지 않음")

        with patch.object(heart_query, "transport", httpx.MockTransport(lambda request: httpx.Response(503))):
            down = client.post(path, json=example)
        check(down.status_code == 502 and 'role="alert"' in down.text, "검색 서버 오류 → 502 안내")
    check(client.post(path, json=example).status_code == 404, "API 주소가 없으면 검색 경로도 닫힘")


def main() -> int:
    keys = [k.lower() for k in sys.argv[1:]] or [
        k for k in DRAFTS if (TEMPLATE_DIR / "drafts" / k / "page.html").exists()
    ]
    unknown = [k for k in keys if k not in DRAFTS]
    if unknown:
        print(f"없는 시안: {unknown}")
        return 2
    with patch.dict(os.environ, {"VEIL_SEARCH_API_URL": ""}), TemporaryDirectory() as temporary_dir:
        check_api()
        for key in keys:
            check_draft(key)
            if key in ("a", "b"):
                check_heart_draft(key)
        connected = [key for key in dict.fromkeys(keys) if key in ("a", "c")]
        if connected:
            pipeline_dir = Path(temporary_dir)
            write_preprocessor_fixture(pipeline_dir)
            for key in connected:
                check_heart_search(key, pipeline_dir)
    print()
    if failures:
        print(f"실패 {len(failures)}건")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("모두 통과" + ("" if keys else " (준비된 시안 없음 — 공통 항목만 검사)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""시안 스모크 테스트 — 서버를 띄우지 않고 TestClient 로 확인한다.

사용 (Blind-Match 폴더에서):
    webui/.venv/Scripts/python webui/tests/smoke.py          # 준비된 시안 전부
    webui/.venv/Scripts/python webui/tests/smoke.py a c      # 지정한 시안만

실패가 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
# Starlette 1.x 가 TestClient 용 httpx 를 httpx2 로 바꾸라고 알리는 경고 — 동작에는 영향 없음
warnings.filterwarnings("ignore", message=".*starlette.testclient.*")

from fastapi.testclient import TestClient  # noqa: E402

from app import backend  # noqa: E402
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
    check(home.status_code == 200 and 'action="/drafts/a/search"' in home.text, "GET / → 의료진 검색 화면")
    check("P023" not in home.text and 'href="/drafts/b?state=empty"' in home.text, "기본 화면은 검색 전 상태이며 발표 모드에 연결")
    archive = client.get("/drafts")
    check(archive.status_code == 200 and all(f'href="/drafts/{key}"' in archive.text for key in ("c", "d")), "보관 목록에서 C·D 열기")
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

    page = client.get(f"/drafts/{key}")
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


def main() -> int:
    keys = [k.lower() for k in sys.argv[1:]] or [
        k for k in DRAFTS if (TEMPLATE_DIR / "drafts" / k / "page.html").exists()
    ]
    unknown = [k for k in keys if k not in DRAFTS]
    if unknown:
        print(f"없는 시안: {unknown}")
        return 2
    check_api()
    for key in keys:
        check_draft(key)
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

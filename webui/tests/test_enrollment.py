import hashlib
import json
import sys
import tempfile
import os
from unittest.mock import patch
import unittest
from pathlib import Path

import httpx
import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import enrollment as institution


class FakeSearchAPI:
    """HTTP contract fixture only; the real API is covered in cross-repository integration."""
    def __init__(self):
        self.active = None
        self.payloads = []

    def request(self, method, path, content=b"", headers=None):
        if method == "GET" and path == "/v1/meta":
            return httpx.Response(200, json={"active": self.active})
        body = json.loads(content)
        self.payloads.append((path, body))
        if path == "/v1/datasets":
            self.data = body
            self.active = {key: body[key] for key in ("dataset_id", "transformer_hash", "name", "scheme", "records", "dimension")}
            return httpx.Response(201, json={"active": self.active, "encrypted": False})
        if path == "/v1/search":
            if not self.active or any(body[key] != self.active[key] for key in ("dataset_id", "transformer_hash")):
                return httpx.Response(409, json={"error": {"code": "dataset_mismatch", "message": "changed"}})
            scores = np.asarray(self.data["vectors"]) @ np.asarray(body["vector"])
            indices = np.argsort(-scores, kind="stable")[:body["top_k"]]
            return httpx.Response(200, json={"dataset_id": self.active["dataset_id"], "encrypted": False, "searched": len(scores), "elapsed_ms": 0,
                "results": [{"rank": rank + 1, "record_id": self.data["ids"][i], "score": float(scores[i])} for rank, i in enumerate(indices)]})
        return httpx.Response(404)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = FakeSearchAPI()

        def dispatch(request):
            res = self.server.request(request.method, request.url.raw_path.decode(),
                                      content=request.content, headers=dict(request.headers))
            return httpx.Response(res.status_code, content=res.content, headers=res.headers)

        self.app = institution.create_app(self.root / "institution", "http://testserver", "test-key", httpx.MockTransport(dispatch))
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.csv = (ROOT / "app" / "data" / "dataset-sample.csv").read_text(encoding="utf-8")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def preview(self, text=None, name="합성 테스트"):
        text = text or self.csv
        inspected = self.client.post("/api/inspect", json={"csv_text": text})
        self.assertEqual(inspected.status_code, 200, inspected.text)
        result = self.client.post("/api/preview", json={"csv_text": text, "name": name, "mapping": inspected.json()["mapping"]})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def register(self, preview):
        res = self.client.post("/api/register", json={"preview_id": preview["preview_id"], "scheme": "plaintext"})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["active"]

    def test_entire_csv_enrollment_and_same_row_search(self):
        preview = self.preview()
        active = self.register(preview)
        self.assertEqual(active["records"], 24)
        self.assertEqual(active["scheme"], "plaintext")
        res = self.client.post("/api/search", json={"dataset_id": active["dataset_id"], "values": preview["sample"][0], "top_k": 5})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(res.json()["encrypted"])
        self.assertEqual(res.json()["results"][0]["record_id"], "R000001")
        self.assertAlmostEqual(res.json()["results"][0]["score"], 1, places=10)
        self.assertEqual(res.json()["searched"], 24)
        self.assertEqual(self.register(preview)["dataset_id"], active["dataset_id"])
        status = self.client.get("/api/status").json()
        self.assertTrue(status["query_ready"])
        enrollment_payload = self.server.payloads[0][1]
        self.assertEqual(set(enrollment_payload), {"dataset_id", "name", "scheme", "transformer_hash", "records", "dimension", "ids", "vectors"})
        self.assertNotIn("csv_text", enrollment_payload)
        self.assertTrue(all(set(row) == set(institution.KEYS) for row in preview["sample"]))


    def test_preview_rows_keep_csv_line_numbers(self):
        # 미리보기 행 번호는 오류 목록의 "N행"과 같은 스프레드시트 행 번호다(머리글이 1행). 빈 줄을 건너뛰어도 어긋나지 않는다.
        self.assertEqual(self.preview()["sample_lines"], [2, 3, 4, 5, 6])
        header, first, *rest = self.csv.splitlines()
        self.assertEqual(self.preview("\n".join([header, "", first, *rest]) + "\n")["sample_lines"], [3, 4, 5, 6, 7])

    def test_column_mapping_and_missing_values(self):
        text = self.csv.replace("age,", "patient_age,", 1)
        text = text.replace("52,", "?,", 1)
        inspected = self.client.post("/api/inspect", json={"csv_text": "\ufeff" + text}).json()
        self.assertEqual(inspected["mapping"]["age"], "")
        inspected["mapping"]["age"] = "patient_age"
        res = self.client.post("/api/preview", json={"csv_text": text, "mapping": inspected["mapping"], "name": "mapped"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertGreater(res.json()["missing_cells"], 0)
        self.register(res.json())

    def test_invalid_inputs_do_not_activate(self):
        inspected = self.client.post("/api/inspect", json={"csv_text": self.csv}).json()
        mapping = inspected["mapping"]
        mapping["age"] = "chol"
        res = self.client.post("/api/preview", json={"csv_text": self.csv, "mapping": mapping, "name": "bad"})
        self.assertEqual(res.status_code, 422)
        bad = self.csv.replace("52,", "NaN,", 1)
        mapping["age"] = "age"
        res = self.client.post("/api/preview", json={"csv_text": bad, "mapping": mapping, "name": "bad"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error"]["issues"][0]["field"], "age")
        self.assertIsNone(self.client.get("/api/status").json()["active"])
        for data in ({}, {"csv_text": ""}, {"csv_text": "a,a\n1,2"}, {"csv_text": "a,b\n1"}, {"csv_text": "age,sex\n"}):
            self.assertEqual(self.client.post("/api/inspect", json=data).status_code, 422)

    def test_unconnected_encryption_fails_without_plaintext_fallback(self):
        preview = self.preview()
        res = self.client.post("/api/register", json={"preview_id": preview["preview_id"], "scheme": "ckks"})
        self.assertEqual(res.status_code, 501)
        self.assertIsNone(self.client.get("/api/status").json()["active"])

    def test_old_snapshot_and_tampered_transformer_rejected(self):
        first = self.preview()
        active1 = self.register(first)
        second = self.preview(name="second")
        active2 = self.register(second)
        res = self.client.post("/api/search", json={"dataset_id": active1["dataset_id"], "values": first["sample"][0], "top_k": 5})
        self.assertEqual(res.status_code, 409)
        # 화면은 문구가 아니라 이 code 로 "다른 화면이 새 등록본을 활성화했다"를 알아본다
        self.assertEqual(res.json()["error"]["code"], "dataset_changed")
        snapshot = self.root / "institution" / active2["dataset_id"]
        (snapshot / "preprocessor.joblib").write_bytes(b"tampered; must not be deserialized")
        res = self.client.post("/api/search", json={"dataset_id": active2["dataset_id"], "values": second["sample"][0], "top_k": 5})
        self.assertEqual(res.status_code, 409)
        self.assertNotIn("code", res.json()["error"])

    def test_receiver_side_dataset_change_reports_dataset_changed(self):
        # 기관의 /v1/meta 확인과 수신 서버 검색 사이에 다른 화면이 등록한 경우 (수신 서버가 409 dataset_mismatch)
        preview = self.preview()
        active = self.register(preview)

        def racing(request):
            if request.url.path == "/v1/search":
                return httpx.Response(409, json={"error": {"code": "dataset_changed", "message": "x"}})
            res = self.server.request(request.method, request.url.raw_path.decode(),
                                      content=request.content, headers=dict(request.headers))
            return httpx.Response(res.status_code, content=res.content, headers=res.headers)

        app = institution.create_app(self.root / "institution", "http://testserver", "test-key", httpx.MockTransport(racing))
        with TestClient(app) as client:
            res = client.post("/api/search", json={"dataset_id": active["dataset_id"], "values": preview["sample"][0], "top_k": 5})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "dataset_changed")

    def test_validation_errors_point_to_the_control(self):
        mapping = self.client.post("/api/inspect", json={"csv_text": self.csv}).json()["mapping"]
        # 이름 오류는 field "name" 으로 1단계의 이름 칸을 가리킨다
        for name in ("   ", "a\tb", "x" * 81):
            res = self.client.post("/api/preview", json={"csv_text": self.csv, "name": name, "mapping": mapping})
            self.assertEqual(res.status_code, 422)
            self.assertEqual([(i["row"], i["field"]) for i in res.json()["error"]["issues"]], [(None, "name")])
        # 정답 · 식별자 열은 어느 속성에 연결했는지 알려 준다
        res = self.client.post("/api/preview", json={"csv_text": self.csv, "name": "x", "mapping": {**mapping, "age": "target"}})
        self.assertEqual(res.status_code, 422)
        self.assertEqual([(i["row"], i["field"]) for i in res.json()["error"]["issues"]], [(None, "age")])

    def test_registration_stages_have_a_sentence_and_a_term(self):
        res = self.client.post("/api/register", json={"preview_id": self.preview()["preview_id"], "scheme": "plaintext"})
        self.assertEqual(res.status_code, 200, res.text)
        stages = res.json()["stages"]
        self.assertTrue(all(stage["say"] and stage["label"] and stage["detail"] for stage in stages))
        self.assertEqual([stage["status"] for stage in stages], ["done", "done", "not_connected", "done"])

    def test_status_lists_registered_categories_and_blank_category_rules(self):
        preview = self.preview()
        active = self.register(preview)
        status = self.client.get("/api/status").json()
        self.assertTrue(status["query_ready"])
        self.assertEqual(status["categories"]["sex"], ["0", "1"])
        self.assertNotIn("__MISSING__", status["categories"]["thal"])
        values = {key: ("" if value is None else value) for key, value in preview["sample"][0].items()}
        # 숫자는 비우면 중앙값으로 채운다
        res = self.client.post("/api/search", json={"dataset_id": active["dataset_id"], "values": {**values, "age": ""}, "top_k": 3})
        self.assertEqual(res.status_code, 200, res.text)
        # 이 등록본에는 범주 결측이 없으므로 범주는 비울 수 없고, 어느 항목인지 알려 준다
        res = self.client.post("/api/search", json={"dataset_id": active["dataset_id"], "values": {**values, "sex": ""}, "top_k": 3})
        self.assertEqual(res.status_code, 422)
        issue = res.json()["error"]["issues"][0]
        self.assertEqual((issue["field"], issue["row"]), ("sex", None))

    def test_failed_registration_leaves_no_orphan_transformer(self):
        def reject(request):
            if request.method == "POST":
                return httpx.Response(422, json={"error": {"code": "invalid_vectors", "message": "x"}})
            return dispatch_to(self.server, request)

        def dispatch_to(server, request):
            res = server.request(request.method, request.url.raw_path.decode(),
                                 content=request.content, headers=dict(request.headers))
            return httpx.Response(res.status_code, content=res.content, headers=res.headers)

        def down(request):
            raise httpx.ConnectError("receiver is down")

        for name, transport in (("rejected", reject), ("down", down)):
            with self.subTest(name):
                folder = self.root / f"institution-{name}"
                app = institution.create_app(folder, "http://testserver", "test-key", httpx.MockTransport(transport))
                with TestClient(app) as client:
                    mapping = client.post("/api/inspect", json={"csv_text": self.csv}).json()["mapping"]
                    preview = client.post("/api/preview", json={"csv_text": self.csv, "name": name, "mapping": mapping}).json()
                    res = client.post("/api/register", json={"preview_id": preview["preview_id"], "scheme": "plaintext"})
                    self.assertIn(res.status_code, (422, 502))
                self.assertEqual(list(folder.iterdir()) if folder.exists() else [], [])

    def test_ambiguous_delivery_keeps_transformer_for_delayed_commit(self):
        self.register(self.preview(name="old"))
        for failure in ("timeout", "invalid_json", "invalid_ack", "server_502"):
            with self.subTest(failure=failure):
                previous_id = self.server.active["dataset_id"]
                pending = []

                def delayed(request):
                    if request.url.path == "/v1/datasets":
                        pending.append(request.content)
                        if failure == "timeout":
                            raise httpx.ReadTimeout("POST remains in progress")
                        if failure == "invalid_json":
                            return httpx.Response(201, content=b"not-json")
                        if failure == "invalid_ack":
                            return httpx.Response(201, json={"active": {}})
                        return httpx.Response(502, json={"error": {"code": "gateway_failure", "message": "lost response"}})
                    return self.server.request(request.method, request.url.path, request.content, request.headers)

                app = institution.create_app(self.root / "institution", "http://testserver", "test-key", httpx.MockTransport(delayed))
                with TestClient(app) as client:
                    mapping = client.post("/api/inspect", json={"csv_text": self.csv}).json()["mapping"]
                    preview = client.post("/api/preview", json={"csv_text": self.csv, "name": failure, "mapping": mapping}).json()
                    response = client.post("/api/register", json={"preview_id": preview["preview_id"], "scheme": "plaintext"})
                    self.assertEqual(response.status_code, 502)
                    self.assertEqual(self.server.active["dataset_id"], previous_id)
                    next_id = json.loads(pending[0])["dataset_id"]
                    self.assertTrue((self.root / "institution" / next_id / "preprocessor.joblib").is_file())
                    # The original POST commits after the failed acknowledgement and reconciliation.
                    self.server.request("POST", "/v1/datasets", pending[0])
                    status = client.get("/api/status").json()
                    self.assertEqual(status["active"]["dataset_id"], next_id)
                    self.assertTrue(status["query_ready"])
                    response = client.post("/api/search", json={"dataset_id": next_id, "values": preview["sample"][0], "top_k": 3})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["results"][0]["record_id"], "R000001")
                    self.assertAlmostEqual(response.json()["results"][0]["score"], 1.0)

    def test_name_with_control_character_rejected_at_validation(self):
        mapping = self.client.post("/api/inspect", json={"csv_text": self.csv}).json()["mapping"]
        res = self.client.post("/api/preview", json={"csv_text": self.csv, "name": "a\tb", "mapping": mapping})
        self.assertEqual(res.status_code, 422)

    def test_search_and_register_screens_are_separate_pages(self):
        for path, marker in (("/", 'data-page="search"'), ("/register", 'data-page="register"')):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200)
            self.assertIn("text/html", res.headers["content-type"])
            self.assertIn(marker, res.text)
        # 정적 파일은 정해 둔 두 개만 내준다 (화면 HTML 은 위의 경로로만)
        self.assertEqual(self.client.get("/static/register.html").status_code, 404)

    def test_inspect_returns_columns_only(self):
        body = self.client.post("/api/inspect", json={"csv_text": self.csv}).json()
        self.assertEqual(set(body), {"columns", "row_count", "mapping"})

    def test_request_boundaries(self):
        self.assertEqual(self.client.post("/api/inspect", json={"csv_text": self.csv}, headers={"origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/inspect", content="{}", headers={"content-type": "text/plain"}).status_code, 415)
        self.assertEqual(self.client.post("/api/inspect", json={"csv_text": "x" * (institution.MAX_CSV_BYTES + 1)}).status_code, 413)
        self.assertEqual(self.client.post("/api/inspect", content="{", headers={"content-type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.post("/api/register", json={"preview_id": "unknown", "scheme": "plaintext"}).status_code, 410)


class IntegrationBoundaryTests(unittest.TestCase):
    def test_mount_navigation_and_assets(self):
        from app.main import app
        with TestClient(app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertIn('/datasets/register', client.get("/").text)
            for path, marker in (("/datasets/", 'data-page="search"'), ("/datasets/register", 'data-page="register"')):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.text)
                self.assertIn('href="static/style.css"', response.text)
            self.assertEqual(client.get("/datasets/static/app.js").status_code, 200)
            self.assertEqual(client.get("/datasets/api/schema").json()["max_rows"], 2000)
            self.assertEqual(client.post("/datasets/api/inspect", json={"csv_text": "age,sex\n52,1"}).status_code, 200)

    def test_pipeline_optional_for_startup_and_validation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"VEIL_PIPELINE_SOURCE_DIR": directory}):
            with TestClient(institution.create_app(Path(directory) / "local", "http://testserver", "key")) as client:
                self.assertEqual(client.get("/register").status_code, 200)
                csv_text = client.get("/api/sample").text
                mapping = client.post("/api/inspect", json={"csv_text": csv_text}).json()["mapping"]
                preview = client.post("/api/preview", json={"csv_text": csv_text, "mapping": mapping, "name": "test"}).json()
                result = client.post("/api/register", json={"preview_id": preview["preview_id"], "scheme": "plaintext"})
                self.assertEqual(result.status_code, 503)
                self.assertIn("VEIL_PIPELINE_SOURCE_DIR", result.json()["error"]["message"])

    def test_nonlocal_api_does_not_break_ui_startup(self):
        with TestClient(institution.create_app(receiver_url="https://example.invalid", api_key="key")) as client:
            self.assertEqual(client.get("/register").status_code, 200)
            self.assertFalse(client.get("/api/status").json()["server_connected"])

    def test_registration_and_search_use_separate_keys(self):
        seen = []
        api = FakeSearchAPI()
        def dispatch(request):
            seen.append((request.url.path, request.headers.get("x-api-key")))
            return api.request(request.method, request.url.path, request.content, request.headers)
        with tempfile.TemporaryDirectory() as directory:
            app = institution.create_app(Path(directory), "http://testserver", "enroll-only", httpx.MockTransport(dispatch), search_api_key="search-only")
            with TestClient(app) as client:
                csv_text = client.get("/api/sample").text
                mapping = client.post("/api/inspect", json={"csv_text": csv_text}).json()["mapping"]
                preview = client.post("/api/preview", json={"csv_text": csv_text, "mapping": mapping, "name": "test"}).json()
                result = client.post("/api/register", json={"preview_id": preview["preview_id"], "scheme": "plaintext"})
                self.assertEqual(result.status_code, 200, result.text)
                result = client.post("/api/search", json={"dataset_id": result.json()["active"]["dataset_id"], "values": preview["sample"][0], "top_k": 3})
                self.assertEqual(result.status_code, 200, result.text)
        self.assertIn(("/v1/datasets", "enroll-only"), seen)
        self.assertIn(("/v1/meta", "search-only"), seen)
        self.assertIn(("/v1/search", "search-only"), seen)


if __name__ == "__main__":
    unittest.main()

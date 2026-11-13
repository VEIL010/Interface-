# VEIL Interface

VEIL의 유사 의료 기록 검색 화면입니다. 기본 화면에서는 환자 특징을 입력해 유사한 기록의 순위를 확인할 수 있습니다. 상단의 **발표 모드**에서는 의료기관과 서버 사이의 목표 처리 흐름과 Top-K 결과를 볼 수 있습니다. C·D 시안은 **보관 시안** 메뉴에 있습니다.

데모는 합성 기록 200건의 평문 코사인 유사도를 계산합니다. 상세 기록 열람과 접근 로그는 로컬 모의 동작이며, CKKS와 HE-C³는 연결 전입니다.

## 로컬 실행

Python 3.12를 기준으로 확인했습니다. 저장소 루트에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r webui/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir webui --host 127.0.0.1 --port 8123
```

브라우저에서 [의료진 검색 화면](http://127.0.0.1:8123/)을 엽니다. macOS와 Linux에서는 `.venv/bin/python`을 사용합니다.

## 스모크 테스트

```powershell
.\.venv\Scripts\python.exe -m pip install -r webui/requirements-dev.txt
.\.venv\Scripts\python.exe webui/tests/smoke.py
```

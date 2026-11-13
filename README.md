# VEIL Interface

VEIL의 의료 기록 검색 인터페이스입니다. 기본 화면(A)에는 Cleveland 특징 13개 입력란이 있습니다. **발표 모드**(B)에서는 예시 입력과 목표 암호화 검색 흐름을 볼 수 있습니다. C·D 시안은 **보관 시안** 메뉴에서 합성 데이터로 실행할 수 있습니다.

## 저장소별 역할

| 저장소 | 역할 |
| --- | --- |
| Interface- | 의료 속성 입력과 검색 결과 표시 |
| Medical-Data-Pipeline | 의료 데이터 전처리와 벡터화 |
| Medical-Search-API | 검색 요청 처리와 검색 서버 연동 |
| BlindMatchCore | CKKS 암호화와 HE-C³ 연산 |

A·B의 실제 검색은 Medical-Search-API의 연동 규격이 준비되면 연결합니다.

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

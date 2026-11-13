# VEIL Interface

VEIL의 의료 기록 검색 인터페이스입니다. 기본 화면(A)에는 Cleveland 특징 13개 입력란이 있습니다. **발표 모드**(B)에서는 예시 입력과 목표 암호화 검색 흐름을 볼 수 있습니다. C·D 시안은 **보관 시안** 메뉴에서 합성 데이터로 실행할 수 있습니다.

## 저장소별 역할

| 저장소 | 역할 |
| --- | --- |
| Interface- | 의료 속성 입력과 검색 결과 표시 |
| Medical-Data-Pipeline | 의료 데이터 전처리와 벡터화 |
| Medical-Search-API | 검색 요청 처리와 검색 서버 연동 |
| BlindMatchCore | CKKS 암호화와 HE-C³ 연산 |

기본 화면(A)의 검색은 Medical-Search-API에 연결할 수 있습니다. 지금은 평문 기준 검색만 연결되어 있고, 암호화 검색은 BlindMatchCore가 준비되면 붙입니다.

## 검색 API 연결

검색 서버 주소와 전처리기가 둘 다 있으면 A 화면의 검색 버튼이 켜집니다. 하나라도 없으면 "검색 API 연결 대기" 상태로 열립니다.

1. Medical-Data-Pipeline에서 `python preprocess.py --pad-to 32 --query-count 20`을 실행하고, 산출물 폴더(`data/processed/heart`)의 내용을 `webui/.pipeline-data/`에 복사합니다. 다른 위치에 두려면 `VEIL_PIPELINE_DIR`로 지정합니다.
2. Medical-Search-API를 같은 산출물 폴더로 실행합니다.
3. 이 서버를 실행하기 전에 주소를 지정합니다.

```powershell
$env:VEIL_SEARCH_API_URL = "http://127.0.0.1:8200"
```

| 환경 변수 | 설명 |
| --- | --- |
| `VEIL_SEARCH_API_URL` | Medical-Search-API 주소. 없으면 검색을 켜지 않습니다 |
| `VEIL_SEARCH_API_KEY` | API가 키를 요구할 때 `X-API-Key`로 보냅니다 |
| `VEIL_PIPELINE_DIR` | 전처리기 폴더. 기본값은 `webui/.pipeline-data` |

입력한 13개 속성은 이 서버에서 벡터로 바뀌고, 검색 서버에는 벡터와 결과 개수만 전달됩니다. `preprocessor.joblib`은 pickle 파일이므로 팀이 직접 만든 것만 둡니다. 이 파일을 만든 scikit-learn 버전과 `webui/requirements.txt`의 버전이 같아야 합니다.

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

"""Field labels and input options for the Cleveland Heart Disease screens."""
from __future__ import annotations


def _field(key, label, en, example, *, unit="", step="1", options=(), required=True, minimum=None, maximum=None):
    return {
        "key": key, "label": label, "en": en, "example": example, "unit": unit,
        "step": step, "options": options, "required": required, "min": minimum, "max": maximum,
    }


FIELDS = (
    _field("age", "나이", "Age", "52", unit="세", minimum=0),
    _field("trestbps", "안정 시 혈압", "Resting BP", "145", unit="mmHg", minimum=0),
    _field("chol", "혈청 콜레스테롤", "Serum cholesterol", "240", unit="mg/dL", minimum=0),
    _field("thalach", "최대 심박수", "Max heart rate", "130", unit="bpm", minimum=0),
    _field("oldpeak", "운동 유발 ST 하강", "ST depression", "1.0", step="any"),
    _field("ca", "조영된 주요 혈관 수", "Major vessels", "0", unit="개", required=False, minimum=0, maximum=3),
    _field("sex", "성별", "Sex", "1", options=(("0", "여성"), ("1", "남성"))),
    _field("cp", "흉통 유형", "Chest pain type", "2", options=(
        ("1", "전형적 협심증"), ("2", "비전형적 협심증"), ("3", "비협심증성 흉통"), ("4", "무증상"),
    )),
    _field("fbs", "공복 혈당 120 mg/dL 초과", "Fasting blood sugar", "0", options=(
        ("0", "아니요"), ("1", "예"),
    )),
    _field("restecg", "안정 시 심전도", "Resting ECG", "0", options=(
        ("0", "정상"), ("1", "ST-T파 이상"), ("2", "좌심실 비대 소견"),
    )),
    _field("exang", "운동 유발 협심증", "Exercise-induced angina", "0", options=(
        ("0", "없음"), ("1", "있음"),
    )),
    _field("slope", "운동 시 ST 구간 기울기", "ST slope", "1", options=(
        ("1", "상향"), ("2", "평탄"), ("3", "하향"),
    )),
    _field("thal", "심근 관류 검사 결과", "Thallium stress test", "3", required=False, options=(
        ("3", "정상"), ("6", "고정 결손"), ("7", "가역 결손"),
    )),
)

# 화면에서 입력란을 묶어 보여 주는 단위. FIELDS 의 순서(파이프라인 입력 순서)는 바꾸지 않는다.
GROUPS = (
    ("기본 정보", ("age", "sex")),
    ("혈압·혈액 검사", ("trestbps", "chol", "fbs")),
    ("증상·심전도", ("cp", "restecg")),
    ("운동 부하 검사", ("thalach", "exang", "oldpeak", "slope")),
    ("영상 검사", ("ca", "thal")),
)


def groups() -> list[dict]:
    by_key = {field["key"]: field for field in FIELDS}
    return [{"title": title, "fields": [by_key[key] for key in keys]} for title, keys in GROUPS]


def form_values(*, example: bool = False) -> dict[str, str]:
    return {field["key"]: field["example"] if example else "" for field in FIELDS}

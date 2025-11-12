"""11 .12데이터베이스 입력을 위한 데이터 정규화 함수들"""

from typing import Any, Optional
from datetime import date


def _normalize_birth_date(birth_date: Any) -> Optional[str]:
    """birthDate를 YYYY-MM-DD 문자열로 변환"""
    if birth_date is None:
        return None
    if isinstance(birth_date, date):
        return birth_date.isoformat()
    if isinstance(birth_date, str):
        if len(birth_date) >= 10:
            return birth_date[:10]
        return birth_date
    return str(birth_date)


def _normalize_insurance_type(insurance_str: str) -> Optional[str]:
    """건강보험 종류를 DB 형식으로 변환 (auth.py에서 이미 매핑된 값 기대)"""
    if not insurance_str:
        return None
    return insurance_str


def _normalize_benefit_type(benefit_str: str) -> str:
    """기초생활보장 급여 종류를 DB 형식으로 변환 (auth.py에서 이미 매핑된 값 기대)"""
    if not benefit_str:
        return "NONE"
    return benefit_str


def _normalize_sex(gender: str) -> Optional[str]:
    """성별을 DB 형식으로 변환 (남성->M, 여성->F 등)"""
    if not gender:
        return None
    gender_lower = gender.lower()
    if "남" in gender_lower or "male" in gender_lower or "m" == gender_lower:
        return "M"
    if "여" in gender_lower or "female" in gender_lower or "f" == gender_lower:
        return "F"
    return gender[:1].upper() if gender else None


def _normalize_disability_grade(disability_level: Any) -> Optional[int]:
    """장애 등급을 정수로 변환"""
    if not disability_level or str(disability_level) in ("0", "미등록"):
        return None
    try:
        return int(disability_level)
    except (ValueError, TypeError):
        return None


def _normalize_ltci_grade(long_term_care: str) -> str:
    """장기요양 등급 정규화"""
    if not long_term_care or long_term_care in ("없음", "해당없음", "NONE"):
        return "NONE"
    return long_term_care.upper()


def _normalize_pregnant_status(pregnancy_status: str) -> Optional[bool]:
    """임신/출산 여부를 Boolean으로 변환"""
    if not pregnancy_status:
        return None
    # bool 타입이 직접 들어오는 경우 처리
    if isinstance(pregnancy_status, bool):
        return pregnancy_status

    status_lower = pregnancy_status.lower()
    if (
        "임신" in status_lower
        or "출산" in status_lower
        or status_lower in ("true", "t")
    ):
        return True
    return False


def _normalize_income_ratio(income_level: Any) -> Optional[float]:
    """소득 수준을 NUMERIC(5,2)로 변환"""
    if income_level is None:
        return None
    try:
        val = float(income_level)
        return round(val, 2)
    except (ValueError, TypeError):
        return None

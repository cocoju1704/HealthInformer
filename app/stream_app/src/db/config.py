"""11.12 환경 변수 로드 및 데이터베이스 연결 설정"""

import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# DB 연결 정보 (오직 환경 변수만 사용)
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT", 5432),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}

# 필수 환경 변수 검사
for key, value in DB_CONFIG.items():
    if value is None and key not in ["port"]: # port는 기본값이 있으므로 제외
        logger.warning(f"환경 변수 '{'DB_' + key.upper()}'가 설정되지 않았습니다.")
        raise ValueError(f"필수 환경 변수 '{'DB_' + key.upper()}'가 누락되었습니다.")
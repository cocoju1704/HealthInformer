""" 11.12
데이터베이스 관련 함수들을 외부에 노출하는 인터페이스 모듈.
실제 구현은 user_repository.py, db_core.py 등 역할별로 분리된 파일에 위임합니다.
"""

from .db_core import get_db_connection
from .user_repository import (
    create_user_and_profile,
    get_user_password_hash,
    get_user_and_profile_by_id,
    get_user_by_username,
    update_user_password,
    update_user_main_profile_id,
    check_user_exists,
    delete_user_account,
    add_profile,
    update_profile,
    delete_profile_by_id,
    get_all_profiles_by_user_id,
)

# 이 파일은 다른 모듈에서 DB 함수를 쉽게 import할 수 있도록 하는 역할을 합니다.
# 예를 들어, `from src.db.database import get_user_by_username` 구문을 그대로 사용할 수 있습니다.
__all__ = [
    "get_db_connection",
    "create_user_and_profile",
    "get_user_password_hash",
    "get_user_and_profile_by_id",
    "get_user_by_username",
    "update_user_password",
    "update_user_main_profile_id",
    "check_user_exists",
    "delete_user_account",
    "add_profile",
    "update_profile",
    "delete_profile_by_id",
    "get_all_profiles_by_user_id",
]

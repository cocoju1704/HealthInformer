"""대화 및 메시지 저장소 관련 기능을 포함하는 모듈 11.20 수정"""

import uuid
import json
from typing import List, Dict, Any
from datetime import datetime, timezone


class ConversationSaveError(Exception):
    """대화 저장 중 발생하는 사용자 정의 예외"""

    pass


def save_full_conversation(
    cursor: Any, # user_id는 conversations 테이블에 직접 저장되지 않으므로 제거
    profile_id: int,
    messages: List[Dict[str, Any]],
) -> str:
    """
    하나의 트랜잭션으로 conversations 테이블과 messages 테이블에 데이터를 저장합니다.

    Args:
        cursor: DB 커서 객체
        user_id: 현재 인증된 사용자 ID
        profile_id: 대화에 사용된 프로필 ID
        messages: 프론트엔드에서 받은 전체 메시지 목록

    Returns:
        저장된 대화의 conversation_id (UUID 문자열)

    Raises:
        ConversationSaveError: DB 저장 실패 시 발생
    """
    if not messages:
        return "no_messages_to_save"

    # 1. 새 conversation_id 생성
    conversation_id = str(uuid.uuid4())

    # 2. 메타데이터 준비
    now = datetime.now(timezone.utc)
    started_at = messages[0].get("timestamp", now.timestamp())
    ended_at = messages[-1].get("timestamp", now.timestamp())

    # JSONB 필드 처리: messages의 policies를 meta 필드로 옮기고 JSON 직렬화
    message_records = []
    for i, msg in enumerate(messages):
        # policies를 meta JSONB 필드에 포함
        meta_data = {}
        if "policies" in msg and msg["policies"] is not None:
            meta_data["policies"] = msg["policies"]

        # tool_name 추출 (role이 'tool'일 경우)
        tool_name = msg.get("tool_name") or (
            msg["content"].split(":")[0].strip()
            if msg["role"] == "tool" and ":" in msg["content"]
            else None
        )

        # 'token_usage'가 있으면 JSONB로 저장
        token_usage_data = msg.get("token_usage")

        message_records.append(
            {
                "id": msg.get("id", str(uuid.uuid4())),
                "conversation_id": conversation_id,
                "turn_index": i,  # 순서는 배열 인덱스를 사용
                "role": msg["role"],
                "content": msg["content"],
                "tool_name": tool_name,
                "token_usage": token_usage_data,  # JSONB 필드
                "meta": meta_data,  # JSONB 필드
                "created_at": datetime.fromtimestamp(
                    msg.get("timestamp", now.timestamp()), tz=timezone.utc
                ),
            }
        )

    # 3. DB 저장 로직 시작 (트랜잭션 권장)
    try:
        # 3-1. conversations 테이블에 새 레코드 삽입
        # summary와 model_stats 등은 초기에는 비워두거나 기본값으로 설정
        cursor.execute(
            """
            INSERT INTO public.conversations 
                (id, profile_id, started_at, ended_at, summary, model_stats, created_at)
            VALUES 
                (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                conversation_id,
                profile_id,
                datetime.fromtimestamp(started_at, tz=timezone.utc),
                datetime.fromtimestamp(ended_at, tz=timezone.utc),
                json.dumps(
                    {"initial_prompt": messages[0].get("content")}
                ),  # 초기 질문만 요약으로 임시 저장
                json.dumps({}),
                now,
            ),
        )

        # 3-2. messages 테이블에 모든 메시지 레코드 삽입
        for record in message_records:
            # PostgreSQL 드라이버에 따라 JSON/JSONB 삽입 방식이 다를 수 있음 (여기서는 json.dumps 사용)
            cursor.execute(
                """
                INSERT INTO public.messages 
                    (id, conversation_id, turn_index, role, content, tool_name, token_usage, meta, created_at)
                VALUES 
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record["id"],
                    record["conversation_id"],
                    record["turn_index"],
                    record["role"],
                    record["content"],
                    record["tool_name"],
                    (
                        json.dumps(record["token_usage"])
                        if record["token_usage"]
                        else None
                    ),
                    json.dumps(record["meta"]),
                    record["created_at"],
                ),
            )

        return conversation_id

    except Exception as e:
        # 로깅 필요
        raise ConversationSaveError(f"DB 저장 트랜잭션 실패: {e}")

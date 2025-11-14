# app/langgraph/nodes/context_assembler.py

from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime, timezone
from langchain_openai import ChatOpenAI

# 템플릿
SUMMARY_PROMPT = """
다음은 지금까지의 대화 요약입니다:
<OLD_SUMMARY>
{old_summary}
</OLD_SUMMARY>

아래는 최근 턴의 메시지들입니다:
<RECENT_MESSAGES>
{recent_messages}
</RECENT_MESSAGES>

사용자가 추후 질문을 해도 컨텍스트를 잃지 않도록,
필요한 정보만 명확하게 요약해 주세요.
"""

def _summarize(old: str, messages: List[Dict[str,Any]]) -> str:
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    recent_text = "\n".join(
        f"{m['role']}: {m['content']}" for m in messages[-6:]
    )

    prompt = SUMMARY_PROMPT.format(
        old_summary=old or "",
        recent_messages=recent_text,
    )
    out = llm.invoke(prompt)
    return out.content.strip()


def assemble(state: Dict[str, Any]) -> Dict[str, Any]:
    messages = state.get("messages", [])
    previous_summary = state.get("rolling_summary")
    turn_count = state.get("turn_count", 1)

    # 15턴마다 summary 업데이트
    should_update = (turn_count % 15 == 0)

    if should_update:
        new_summary = _summarize(previous_summary, messages)
    else:
        new_summary = previous_summary

    # 다음 노드를 위해 prompt_ready 형태의 메시지 추가
    assembled_prompt = {
        "role": "tool",
        "content": "[context_assembler] prompt_ready",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "summary_updated": should_update,
            "turn_count": turn_count,
        }
    }

    return {
        "rolling_summary": new_summary,
        "messages": messages + [assembled_prompt],
    }

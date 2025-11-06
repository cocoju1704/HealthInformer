"""
통합 헬스케어 챗봇: DB 연결 + Agentic RAG

1. 데이터베이스 연결: PostgreSQL에서 건강 지원 정보 조회
2. agent.py 기능: PGVector 벡터 스토어 + 검색 도구 + 멀티턴 대화
"""

import os
import sys
import asyncio
from typing import List, Dict, Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_community.vectorstores import PGVector
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_core.documents import Document
from sqlalchemy import create_engine, text


# 환경 변수 로드
load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMP = float(os.getenv("TEMPERATURE", "0.2"  ))

CONNECTION_STRING = (
    f"postgresql://{os.getenv('DB_USER')}:"
    f"{os.getenv('DB_PASSWORD')}@"
    f"{os.getenv('DB_HOST')}:"
    f"{os.getenv('DB_PORT')}/"
    f"{os.getenv('DB_NAME')}"
)

class HealthCareChatbot:
    """통합 헬스케어 챗봇 - DB 연결 + RAG 검색 + 대화"""

    def __init__(
        self, 
        region: Optional[str] = None,
        database_url: Optional[str] = None
    ):
        """
        Args:
            region: 지역명 필터 (None이면 전체 지역)
            database_url: PostgreSQL 연결 URL (None이면 환경변수에서 읽음)
        """
        self.region = region
        self.structured_data = []
        self.vector_store = None
        self.agent_executor = None
        self.conversation_region = None  # 대화 시 사용할 지역명

        # database_url 저장 (직접 연결에서 사용)
        self.database_url = database_url or CONNECTION_STRING

        # SQLAlchemy 엔진 생성
        self.engine = create_engine(self.database_url)

        # 임베딩 모델명 공유 (저장/로드 시 동일해야 함)
        self.embedding_model_name = 'text-embedding-3-small'

    def load_data(self, region: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """
        documents 테이블에서 데이터 로드

        Args:
            region: 지역명 필터 (None이면 self.region 또는 전체)
            limit: 최대 개수 (None이면 전체)

        Returns:
            구조화된 데이터 리스트
        """
        region_filter = region or self.region

        print(f"\n📂 데이터베이스에서 데이터 로드 중...")
        if region_filter:
            print(f"  → 지역 필터: {region_filter}")
        if limit:
            print(f"  → 최대 개수: {limit}")

        # SQL 쿼리 작성
        query = "SELECT id, title, requirements, benefits, region, url FROM documents"
        params = {}
        
        if region_filter:
            query += " WHERE region = :region"
            params["region"] = region_filter
        
        if limit:
            query += " LIMIT :limit"
            params["limit"] = limit

        # 데이터베이스에서 데이터 조회
        with self.engine.connect() as conn:
            result = conn.execute(text(query), params)
            rows = result.fetchall()
            
            # 결과를 딕셔너리 리스트로 변환
            self.structured_data = []
            for row in rows:
                self.structured_data.append({
                    "id": row[0],
                    "title": row[1],
                    "requirements": row[2],
                    "benefits": row[3],
                    "region": row[4],
                    "url": row[5]
                })

        print(f"✅ {len(self.structured_data)}개 문서 로드 완료")

        return self.structured_data

    def load_vector_store(self) -> Optional[bool]:
        """
        embeddings 테이블 존재 확인

        Returns:
            성공 시 True, 실패 시 None
        """
        print("\n" + "=" * 80)
        print("📦 embeddings 테이블 확인 중...")
        print("=" * 80)

        try:
            # embeddings 테이블 존재 및 데이터 확인
            with self.engine.connect() as conn:
                result = conn.execute(text(
                    "SELECT COUNT(*) FROM embeddings"
                ))
                count = result.scalar()
                
                if count == 0:
                    print("⚠️  embeddings 테이블이 비어있습니다.")
                    return None
                
                print(f"✅ embeddings 테이블 확인 완료 ({count}개의 임베딩)\n")
                self.vector_store = True  # 벡터 스토어 사용 가능 표시
                return True
                
        except Exception as e:
            print(f"⚠️  embeddings 테이블 확인 실패: {e}")
            return None

    def setup_agent(self):
        """
        LangChain 에이전트 설정 (agent.py 기능)
        """
        if not self.vector_store:
            raise ValueError(
                "벡터 스토어가 없습니다. 먼저 build_vector_store()를 실행하세요."
            )

        print("🤖 에이전트 설정 중...")

        @tool
        def search_with_score(query: str) -> str:
            """
            건강 지원 정보 데이터베이스에서 유사도 점수와 함께 검색합니다.
            """
            try:
                # OpenAI로 query 임베딩
                embeddings = OpenAIEmbeddings(model=self.embedding_model_name)
                query_embedding = embeddings.embed_query(query)
                
                # PostgreSQL에서 유사도 검색 (pgvector 사용)
                with self.engine.connect() as conn:
                    result = conn.execute(text("""
                        SELECT 
                            d.id, d.title, d.requirements, d.benefits, d.region, d.url,
                            1 - (e.embedding <=> CAST(:query_embedding AS vector)) as similarity
                        FROM documents d
                        JOIN embeddings e ON d.id = e.doc_id
                        ORDER BY e.embedding <=> CAST(:query_embedding AS vector)
                        LIMIT 7
                    """), {"query_embedding": str(query_embedding)})
                    
                    rows = result.fetchall()

                if not rows:
                    return "검색 결과가 없습니다."

                out = []
                for i, row in enumerate(rows, start=1):
                    # title + requirements + benefits 조합
                    text_content = f"{row[1]}\n요건: {row[2]}\n혜택: {row[3]}"
                    preview = text_content[:200].replace("\n", " ")

                    out.append(
                        f"[문서 {i} | 점수: {row[6]:.4f}]\n"
                        f"제목: {row[1]}\n"
                        f"지역: {row[4]}\n"
                        f"내용: {preview}...\n"
                        f"URL: {row[5]}\n"
                    )

                return "\n".join(out)
            except Exception as e:
                return f"검색 중 오류가 발생했습니다: {e}"

        tools = [search_with_score]

        # 프롬프트 설정
        SYSTEM_PROMPT = """당신은 보건소 건강 지원 정보를 안내하는 전문 상담원입니다.

지침:
- 사용자의 질문에 대해 검색 도구를 사용하여 관련 정보를 찾을 것
- 검색 결과를 바탕으로 명확하고 친절하게 답변할 것
- 지원 대상, 지원 내용, 신청 방법 등 핵심 정보를 간결하게 요약할 것
- 여러 지역의 정보가 있다면 지역별로 구분하여 안내해야하며 만약 제공된 문서에 세부 지원 내용이 존재한다면 그 내용을 기반으로 답변할 것
- 정보가 부족하면 "해당 정보를 찾을 수 없습니다"라고 솔직히 답변할 것
- 예시로 질문 : 암 지원에 대해 알려줘 인 경우 제공 문서에 암 지원이 없으면 참조 하지 않을 것
- 답변 끝에는 출처 URL을 제공하세요.
"""

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SYSTEM_PROMPT),
                MessagesPlaceholder(variable_name="chat_history"),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        # LLM 및 에이전트 생성
        llm = ChatOpenAI(model=MODEL, temperature=TEMP, streaming=True)
        agent = create_openai_tools_agent(llm, tools, prompt)

        self.agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            handle_parsing_errors=True,
            max_iterations=5,
        )

        print("✅ 에이전트 설정 완료\n")

    def print_summary(self):
        """데이터베이스에서 로드된 데이터 요약 출력"""
        if not self.structured_data:
            print("⚠️  로드된 데이터가 없습니다.")
            return

        print("\n" + "=" * 80)
        print("📊 데이터베이스 데이터 요약")
        print("=" * 80)

        # 지역별 통계
        region_count = {}
        for item in self.structured_data:
            region = item.get("region", "미지정")
            region_count[region] = region_count.get(region, 0) + 1

        print(f"\n총 문서 수: {len(self.structured_data)}개")
        print("\n지역별 분포:")
        for region, count in region_count.items():
            print(f"  - {region}: {count}개")

        print("\n최근 문서 3개:")
        for i, item in enumerate(self.structured_data[:3], 1):
            print(f"\n  [{i}] {item.get('title', 'N/A')}")
            print(f"      지역: {item.get('region', 'N/A')}")
            print(f"      URL: {item.get('url', 'N/A')}")

        print("\n" + "=" * 80)

    async def run_conversation(self):
        """
        멀티턴 대화 실행 (agent.py 기능)
        """
        if not self.agent_executor:
            raise ValueError(
                "에이전트가 설정되지 않았습니다. 먼저 setup_agent()를 실행하세요."
            )

        chat_history = []

        # 요약 정보 출력
        self.print_summary()

        print("\n" + "=" * 80)
        print("💬 헬스케어 챗봇 (건강 지원 정보 상담)")
        print("=" * 80)
        print("종료: quit/exit/종료 | 초기화: reset/clear/초기화")
        print("=" * 80)

        while True:
            user_input = await asyncio.to_thread(input, "종료를 원하시면 종료/exit/quit 입력\n초기화를 원하시면 초기화/reset/clear 입력\n질문: ")
            if user_input is None:
                continue
            user_input = user_input.strip()

            # 종료 명령
            if user_input.lower() in ["exit", "quit", "종료"]:
                print("\n👋 시스템을 종료합니다.")
                break

            # 초기화 명령
            if user_input.lower() in ["reset", "clear", "초기화"]:
                chat_history = []
                print("\n🔄 대화 내용이 초기화되었습니다.")
                self.print_summary()
                continue

            if not user_input:
                continue

            try:
                print("답변: ", end="", flush=True)
                full_response = ""

                # 스트리밍 응답
                async for event in self.agent_executor.astream_events(
                    {"input": user_input, "chat_history": chat_history},
                    version="v2",
                ):
                    kind = event["event"]

                    if kind == "on_tool_start":
                        tool_name = event["name"]
                        print(f"\n[🔍 {tool_name} 검색 중...]", end="", flush=True)
                        print("\n답변: ", end="", flush=True)

                    elif kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"].content
                        if chunk:
                            print(chunk, end="", flush=True)
                            full_response += chunk

                print()  # 줄바꿈

                # 대화 기록 업데이트
                chat_history.append(HumanMessage(content=user_input))
                chat_history.append(AIMessage(content=full_response))

            except Exception as e:
                print(f"\n❌ 오류 발생: {e}")

    def initialize(
        self,
        region: Optional[str] = None,
        limit: Optional[int] = None,
    ):
        """
        챗봇 초기화 (전체 파이프라인)

        Args:
            region: 지역명 필터 (None이면 전체 지역)
            limit: 최대 데이터 개수 (None이면 전체)
        """
        print("\n" + "=" * 80)
        print("🚀 헬스케어 챗봇 초기화")
        print("=" * 80)

        # 1. 데이터베이스에서 데이터 로드
        print("\n[1] 데이터베이스에서 데이터 로드")
        self.load_data(region=region, limit=limit)

        if not self.structured_data:
            raise ValueError(
                "데이터베이스에서 데이터를 가져올 수 없습니다.\n"
                "DB 연결 정보와 데이터 존재 여부를 확인하세요."
            )

        # 2. 벡터 스토어 로드
        print("\n[2] 벡터 스토어 로드")
        loaded = self.load_vector_store()
        
        if loaded is None:
            raise ValueError(
                "데이터베이스에서 벡터 스토어를 로드할 수 없습니다.\n"
                "벡터 인덱스가 이미 구축되어 있는지 확인하세요."
            )

        # 3. 에이전트 설정
        print("\n[3] 에이전트 설정")
        self.setup_agent()

        print("\n" + "=" * 80)
        print("✅ 초기화 완료! 이제 대화를 시작할 수 있습니다.")
        print("=" * 80)


def main():
    """메인 실행 함수"""
    import argparse

    parser = argparse.ArgumentParser(
        description="통합 헬스케어 챗봇 - DB 연결 + RAG + 대화"
    )
    parser.add_argument("--region", type=str, help="지역명 필터 (예: 강남구)")
    parser.add_argument(
        "--limit",
        type=int,
        help="최대 데이터 개수 (None이면 전체)",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        help="PostgreSQL 연결 URL (환경변수에서 읽음)",
    )

    args = parser.parse_args()

    # 챗봇 생성 및 초기화
    try:
        chatbot = HealthCareChatbot(
            region=args.region,
            database_url=args.database_url
        )

        chatbot.initialize(
            region=args.region,
            limit=args.limit,
        )

        # 대화 시작
        asyncio.run(chatbot.run_conversation())

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()

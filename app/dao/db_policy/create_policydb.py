# -*- coding: utf-8 -*-
"""
create_policydb.py (현재 프레임워크 호환 버전)
- documents / embeddings 테이블 구조를 현재 파이프라인에 맞게 생성 또는 보정
- embeddings.embedding: VECTOR(1536)
- llm_reinforced 관련 컬럼 자동 추가
"""

import os
import sys
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

DIM = 1536  # text-embedding-3-small 기준

# ─────────────────────────────────────────────
# 1) DSN Builder
# ─────────────────────────────────────────────
def build_dsn():
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    pwd  = os.getenv("DB_PASSWORD")
    if not all([name, user, pwd]):
        raise RuntimeError("DATABASE_URL 또는 (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)가 필요합니다.")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{name}"


# ─────────────────────────────────────────────
# 2) pgvector 확장 보장
# ─────────────────────────────────────────────
def ensure_pgvector(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    conn.commit()
    print("✅ pgvector extension ensured")


# ─────────────────────────────────────────────
# 3) documents 테이블 보장
# ─────────────────────────────────────────────
def ensure_documents_table(conn):
    sql = """
    CREATE TABLE IF NOT EXISTS documents (
        id BIGSERIAL PRIMARY KEY,
        title TEXT,
        requirements TEXT,
        benefits TEXT,
        raw_text TEXT,
        url TEXT,
        policy_id BIGINT,
        region TEXT,
        sitename TEXT,
        weight INTEGER DEFAULT 1,
        llm_reinforced BOOLEAN DEFAULT FALSE,
        llm_reinforced_sources JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print("✅ documents table ensured")


# ─────────────────────────────────────────────
# 4) embeddings 테이블 보장
# ─────────────────────────────────────────────
def ensure_embeddings_table(conn, dim=DIM):
    with conn.cursor() as cur:
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS embeddings (
            id BIGSERIAL PRIMARY KEY,
            doc_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            field TEXT NOT NULL CHECK (field IN ('title','requirements','benefits')),
            embedding VECTOR({dim}) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (doc_id, field)
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_doc_field ON embeddings (doc_id, field);
        """)
    conn.commit()
    print(f"✅ embeddings table ensured (VECTOR({dim}))")


# ─────────────────────────────────────────────
# 5) 컬럼 동기화 (llm_reinforced 등 추가)
# ─────────────────────────────────────────────
def ensure_columns(conn):
    with conn.cursor() as cur:
        cur.execute("""
        ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS llm_reinforced BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS llm_reinforced_sources JSONB,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
        """)
    conn.commit()
    print("✅ documents columns synced")


# ─────────────────────────────────────────────
# 6) 마이그레이션 확인 (double precision[] → VECTOR)
# ─────────────────────────────────────────────
def migrate_embedding_array_to_vector(conn, table="embeddings", column="embedding", dim=DIM):
    with conn.cursor() as cur:
        cur.execute(f"""
        DO $$
        DECLARE t regclass := '{table}'::regclass;
        DECLARE c regclass;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{table}' AND column_name = '{column}' AND data_type = 'ARRAY'
            ) THEN
                RAISE NOTICE 'Migrating {table}.{column} to VECTOR({dim})...';
                EXECUTE format(
                    'ALTER TABLE %I ALTER COLUMN %I TYPE vector(%s) USING (''['' || array_to_string(%I, '','') || '']'')::vector',
                    '{table}', '{column}', {dim}, '{column}'
                );
            END IF;
        END$$;
        """)
    conn.commit()


# ─────────────────────────────────────────────
# 7) main
# ─────────────────────────────────────────────
def main():
    dsn = build_dsn()
    conn = psycopg2.connect(dsn)
    try:
        ensure_pgvector(conn)
        ensure_documents_table(conn)
        ensure_embeddings_table(conn)
        ensure_columns(conn)
        migrate_embedding_array_to_vector(conn)
        print("🎯 policy database initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"❌ Error: {e}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

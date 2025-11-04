# app/crawling/dbsetup_pipeline.py
# 목적: district, welfare, ehealth 크롤러 → DB 업로드 → policy_id 그루핑
# 중간 JSON 없이, 메모리에서 바로 documents/embeddings에 삽입
# 진행률(%) 출력 추가 버전

import os, sys, argparse, traceback, json
from datetime import datetime
import psycopg2
from psycopg2.extras import execute_values
from openai import OpenAI
from dotenv import load_dotenv

# 프로젝트 루트 경로 보정
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, PROJECT_ROOT)

from app.crawling.crawlers.district_crawler import HealthCareWorkflow
from app.crawling.crawlers.welfare_crawler import WelfareCrawler
from app.crawling.crawlers.ehealth_crawler import EHealthCrawler
from app.crawling.crawlers import run_all_crawlers as rac
from app.dao.db_policy import dbuploader_policy as dbuploader
from app.dao.db_policy import dbgrouper_policy as dbgrouper
from app.dao.utils_db import eprint, extract_sitename_from_url, get_weight


def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)
    return p


# ─────────────────────────────────────────────
# 수집 함수들
# ─────────────────────────────────────────────
def collect_district(urls, out_dir):
    all_data = []
    for url in urls:
        wf = HealthCareWorkflow(output_dir=out_dir)
        summary = wf.run(start_url=url, save_links=True, save_json=False, return_data=True)
        all_data.extend(summary.get("data", []))
    return all_data


def collect_welfare(out_dir, no_filter=False, max_items=None):
    crawler = WelfareCrawler(output_dir=out_dir)
    data = crawler.run_workflow(filter_health=not no_filter, max_items=max_items,
                                return_data=True, save_json=False)
    return data or []


def collect_ehealth(out_dir, categories=None, max_pages=None):
    crawler = EHealthCrawler(output_dir=out_dir)
    data = crawler.run_workflow(categories=categories, max_pages_per_category=max_pages,
                                return_data=True, save_json=False)
    return data or []


# ─────────────────────────────────────────────
# DB 업로드 (진행률 % 표시 추가)
# ─────────────────────────────────────────────
def upload_records(records, reset="none", emb_model="text-embedding-3-small", commit_every=50):
    if not records:
        eprint("[upload] 업로드할 레코드가 없습니다.")
        return

    preprocess_title = dbuploader.preprocess_title
    get_embedding = dbuploader.get_embedding

    load_dotenv()
    DB_URL = os.getenv("DATABASE_URL")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if not DB_URL or not OPENAI_API_KEY:
        raise RuntimeError("DATABASE_URL, OPENAI_API_KEY 환경변수가 필요합니다.")
    _ = OpenAI(api_key=OPENAI_API_KEY)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    total = len(records)
    bar_length = 40  # 진행률 바 길이

    try:
        if reset != "none":
            cur.execute("TRUNCATE TABLE embeddings, documents RESTART IDENTITY CASCADE;")
            conn.commit()
            print(f"✅ 테이블 리셋 완료: {reset}")

        inserted = 0
        for idx, item in enumerate(records, 1):
            title = item.get("title", "")
            requirements = item.get("support_target", "")
            benefits = item.get("support_content", "")
            raw_text = item.get("raw_text", "")
            url = item.get("source_url", "")
            region = item.get("region", "")
            sitename = extract_sitename_from_url(url)
            weight = get_weight(region, sitename)

            # documents
            cur.execute("""
                INSERT INTO documents (title, requirements, benefits, raw_text, url, policy_id, region, sitename, weight, llm_reinforced, llm_reinforced_sources)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """, (title, requirements, benefits, raw_text, url, None, region, sitename, weight, False, None))
            doc_id = cur.fetchone()[0]

            # embeddings
            emb_rows = []
            title_emb_text = preprocess_title(title)
            for fname, text_value in (
                ("title", title_emb_text),
                ("requirements", requirements),
                ("benefits", benefits),
            ):
                vec = get_embedding(text_value, emb_model)
                if vec:
                    emb_rows.append((doc_id, fname, vec))

            if emb_rows:
                execute_values(
                    cur,
                    "INSERT INTO embeddings (doc_id, field, embedding) VALUES %s",
                    emb_rows,
                    template="(%s, %s, %s)"
                )

            inserted += 1
            percent = (inserted / total) * 100
            filled = int(bar_length * percent / 100)
            bar = "█" * filled + "-" * (bar_length - filled)
            sys.stdout.write(f"\r[Upload] |{bar}| {percent:6.2f}% ({inserted}/{total})")
            sys.stdout.flush()

            if inserted % commit_every == 0:
                conn.commit()

        conn.commit()
        print(f"\n🎉 업로드 완료! 총 {inserted}건 삽입")

    except Exception as e:
        conn.rollback()
        eprint(f"[upload] 에러로 롤백: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ─────────────────────────────────────────────
# 그룹핑
# ─────────────────────────────────────────────
def group_policies(threshold=0.85, batch_size=500, reset_all=False, verbose=True):
    res = dbgrouper.assign_policy_ids(
        title_field="title",
        similarity_threshold=threshold,
        batch_size=batch_size,
        dry_run=False,
        reset_all_on_start=reset_all,
        verbose=verbose,
    )
    return res


def _get_runall_urls():
    for name in ["TARGET_URLS", "DISTRICT_TARGETS", "DEFAULT_URLS", "URLS"]:
        if hasattr(rac, name):
            v = getattr(rac, name)
            try:
                return list(v)
            except Exception:
                pass
    return []


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="통합 크롤링 → DB 업로드 → policy_id 그루핑 (in-memory, 진행률 표시)")
    p.add_argument("--source", choices=["district", "welfare", "ehealth", "all"], default="district")
    p.add_argument("--urls", nargs="*", help="district 시작 URL들")
    p.add_argument("--out-dir", default=os.path.join(PROJECT_ROOT, "app", "crawling", "output"))
    p.add_argument("--reset", choices=["none", "truncate"], default="none")
    p.add_argument("--group", action="store_true")
    p.add_argument("--threshold", type=float, default=0.85)
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--use-runall-targets", action="store_true", help="district 수집 시 run_all_crawlers.py의 URL 목록 사용")
    args = p.parse_args()

    _ensure_dir(args.out_dir)

    try:
        mem_data = []

        if args.source in ("district", "all"):
            if args.use_runall_targets:
                urls = _get_runall_urls()
                if not urls:
                    eprint("[district] run_all_crawlers.py에서 URL을 찾지 못했어요. --urls 인자를 사용하세요.")
                    urls = args.urls or []
            else:
                urls = args.urls or [
                    "https://health.gangnam.go.kr/web/business/support/sub01.do",
                    "https://health.gangdong.go.kr/health/site/main/content/GD20030100",
                    "https://www.gangbuk.go.kr/health/main/contents.do?menuNo=400151",
                    "https://www.gangseo.seoul.kr/health/ht020231",
                    "https://www.gwanak.go.kr/site/health/05/10502010600002024101710.jsp",
                    "https://www.gwangjin.go.kr/health/main/contents.do?menuNo=300080",
                    "https://www.guro.go.kr/health/contents.do?key=1320&",
                    "https://www.dongjak.go.kr/healthcare/main/contents.do?menuNo=300342",
                    "https://www.sdm.go.kr/health/contents/infectious/law",
                    "https://www.seocho.go.kr/site/sh/03/10301000000002015070902.jsp",
                    "https://www.sb.go.kr/bogunso/contents.do?key=6553",
                    "https://www.ydp.go.kr/health/contents.do?key=6073&",
                    "https://www.songpa.go.kr/ehealth/contents.do?key=4525&",
                    "https://jongno.go.kr/Health.do?menuId=401309&menuNo=401309",
                ]

            eprint(f"[district] {len(urls)}개 URL 처리 (메모리 수집)")
            mem_data += collect_district(urls, args.out_dir)

        if args.source in ("welfare", "all"):
            mem_data += collect_welfare(args.out_dir)

        if args.source in ("ehealth", "all"):
            mem_data += collect_ehealth(args.out_dir)

        if not mem_data:
            eprint("❌ 수집된 데이터가 없습니다.")
            return

        eprint(f"[upload] 메모리 레코드 {len(mem_data)}건 업로드 중…")
        upload_records(mem_data, reset=args.reset)

        if args.group:
            eprint("[group] policy_id 그루핑 시작")
            result = group_policies(args.threshold, args.batch_size)
            print("[group result]", result)

        print("\n✅ 완료:", len(mem_data), "records")

    except Exception as e:
        traceback.print_exc()
        eprint(f"오류 발생: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

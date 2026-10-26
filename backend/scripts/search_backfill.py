"""search_backfill.py — DB의 Chunk 테이블 → 검색 인덱스(OpenSearch) 재색인

[2026-07-28] turbovec에서 검색 엔진으로 넘어오면서 만든 백필 스크립트(구 es_backfill.py).
[2026-07-31] 배포 환경에서 검색 엔진에 영속 볼륨을 붙일 수 없어 재시작마다 인덱스가 비는
문제 때문에 run_backfill()을 main.py의 자동 복구 태스크에서도 재사용한다.
[2026-10-24] Elasticsearch → OpenSearch 교체에 맞춰 이름과 클라이언트를 바꿨다. 임베딩 모델을
바꿨을 때 쓰는 --reembed 옵션을 추가했다: DB에 저장된 청크 원문을 현재 모델(EMBED_MODEL)로
다시 임베딩해 DB의 Chunk.embedding까지 갱신한 뒤 색인한다.

CLI (backend/ 디렉토리에서):
    PYTHONUTF8=1 .venv/Scripts/python scripts/search_backfill.py            # 저장된 벡터로 재색인
    PYTHONUTF8=1 .venv/Scripts/python scripts/search_backfill.py --reembed  # 현재 모델로 벡터부터 다시 만듦
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Chunk, Document
from search import opensearch_client


async def run_backfill(reembed: bool = False) -> tuple[int, int]:
    """DB의 모든 Chunk를 검색 인덱스에 재색인한다. 반환: (성공 건수, 실패 건수).
    검색 엔진 연결 자체가 안 되면 (0, -1) — 색인 시도조차 못 한 것과 색인 실패를 구분한다."""
    if not opensearch_client.ensure_index():
        print("[백필] 검색 엔진(OpenSearch)에 연결할 수 없습니다 — 컨테이너가 떠 있는지 확인하세요.")
        return (0, -1)

    async with AsyncSessionLocal() as sess:
        doc_meta = {d.id: d for d in (await sess.execute(
            select(Document.id, Document.filename, Document.title, Document.memo)
        )).all()}
        chunks = (await sess.execute(select(Chunk).order_by(Chunk.id))).scalars().all()

        print(f"[백필] 대상 청크 {len(chunks)}개 (문서 {len(doc_meta)}개)")
        if reembed and chunks:
            from search import embedder
            vecs = await asyncio.to_thread(embedder.encode_passages, [c.text for c in chunks])
            if vecs is None:
                print("[백필] 임베딩 모델을 불러오지 못해 --reembed를 중단합니다.")
                return (0, len(chunks))
            for c, v in zip(chunks, vecs):
                c.embedding = v.tobytes()
            await sess.commit()
            print(f"[백필] {embedder.EMBED_MODEL}로 청크 {len(chunks)}개를 다시 임베딩해 DB에 저장")
            chunks = (await sess.execute(select(Chunk).order_by(Chunk.id))).scalars().all()

    rows, skipped_no_emb = [], 0
    for c in chunks:
        meta = doc_meta.get(c.doc_id)
        embedding = None
        if c.embedding is not None:
            embedding = np.frombuffer(c.embedding, dtype=np.float32).tolist()
        else:
            skipped_no_emb += 1
        rows.append({
            "chunk_id": c.id,
            "doc_id": c.doc_id,
            "filename": meta.filename if meta else "",
            "title": meta.title if meta else "",
            "memo": meta.memo if meta else "",
            "text": c.text,
            "page_num": c.page_num,
            "chunk_idx": c.chunk_idx,
            "embedding": embedding,
        })

    batch_size, total_ok, total_failed = 500, 0, 0
    for i in range(0, len(rows), batch_size):
        ok, failed = opensearch_client.bulk_index_chunks(rows[i:i + batch_size])
        total_ok += ok
        total_failed += failed
        done = min(i + batch_size, len(rows))
        print(f"[백필] {done}/{len(rows)} — 성공 {ok}건 / 실패 {failed}건" if failed
              else f"[백필] {done}/{len(rows)} 색인 완료")

    if skipped_no_emb:
        print(f"[백필] 참고: embedding이 없는 청크 {skipped_no_emb}개는 BM25로만 검색됨(kNN 대상 아님)")
    if total_failed:
        print(f"[백필] 실패 — 색인 성공 {total_ok}건 / 실패 {total_failed}건 (실패한 청크는 검색에 안 잡힙니다)")
    else:
        print(f"[백필] 완료 — 색인 성공 {total_ok}건")
    return (total_ok, total_failed)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reembed", action="store_true", help="현재 EMBED_MODEL로 청크 벡터를 다시 만든 뒤 색인")
    args = ap.parse_args()
    ok, failed = await run_backfill(reembed=args.reembed)
    return 1 if failed != 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

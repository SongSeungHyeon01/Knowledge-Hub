"""es_backfill.py — 기존 PostgreSQL/SQLite Chunk 테이블 → Elasticsearch 백필

[2026-07-28] turbovec+npz에서 Elasticsearch로 넘어오면서, 마이그레이션 전에 이미
업로드된 문서들의 청크(Chunk.embedding에 원본 벡터가 남아 있음)를 ES에 새로 색인한다.

[2026-07-31 정정] "ES는 자체 저장소라 매번 재구성할 필요가 없다"는 원래 전제가
이 배포 환경(Railway)에서는 깨졌다 — 컨테이너 실행 권한 문제로 ES에 영속 볼륨을
붙일 수 없어(docs/오류보고서 참고), ES가 재시작될 때마다 인덱스가 통째로 비워진다.
그래서 이 파일의 핵심 로직을 `run_backfill()`로 분리해 main.py의 주기적 자동 복구
태스크(`_es_selfheal_task`)에서도 재사용한다 — "한 번만 수동 실행"이 아니라
"필요할 때마다 자동/수동 둘 다" 실행 가능해야 하기 때문.

CLI로 수동 실행하는 방법 (backend/ 디렉토리에서):
    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 venv/Scripts/python.exe scripts/es_backfill.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from database import AsyncSessionLocal
from models import Chunk, Document
from search import es_client


async def run_backfill() -> tuple[int, int]:
    """Postgres의 모든 Chunk를 Elasticsearch에 재색인한다. 반환: (성공 건수, 실패 건수).
    ES 연결 자체가 안 되면 (0, -1)을 반환한다(구분을 위해 -1 — 색인 시도조차 못 한 것과
    "색인은 시도했지만 실패"를 호출부가 구분할 수 있게)."""
    if not es_client.ensure_index():
        print("[백필] Elasticsearch에 연결할 수 없습니다 — 컨테이너가 떠 있는지 확인하세요.")
        return (0, -1)

    async with AsyncSessionLocal() as sess:
        doc_rows = (await sess.execute(
            select(Document.id, Document.filename, Document.title, Document.memo)
        )).all()
        doc_meta = {d.id: d for d in doc_rows}

        chunks = (await sess.execute(select(Chunk))).scalars().all()

    print(f"[백필] 대상 청크 {len(chunks)}개 (문서 {len(doc_meta)}개)")

    rows = []
    skipped_no_emb = 0
    for c in chunks:
        meta = doc_meta.get(c.doc_id)
        embedding = None
        if c.embedding is not None:
            import numpy as np
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

    BATCH = 500
    total_ok = 0
    total_failed = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        ok, failed = es_client.bulk_index_chunks(batch)
        total_ok += ok
        total_failed += failed
        done = min(i + BATCH, len(rows))
        if failed:
            print(f"[백필] {done}/{len(rows)} — 성공 {ok}건 / 실패 {failed}건")
        else:
            print(f"[백필] {done}/{len(rows)} 색인 완료")

    if skipped_no_emb:
        print(f"[백필] 참고: embedding이 없는 청크 {skipped_no_emb}개는 BM25로만 검색됨(kNN 대상 아님)")

    if total_failed:
        print(f"[백필] 실패 — 색인 성공 {total_ok}건 / 실패 {total_failed}건 (실패한 청크는 검색에 안 잡힙니다)")
    else:
        print(f"[백필] 완료 — 색인 성공 {total_ok}건")
    return (total_ok, total_failed)


async def main() -> int:
    """CLI 전용 진입점 — run_backfill()을 호출하고 성공/실패를 프로세스 종료 코드로 변환한다."""
    ok, failed = await run_backfill()
    return 1 if failed != 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

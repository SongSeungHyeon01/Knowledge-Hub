"""es_client.py — Elasticsearch 연결 / 인덱스 관리 / 하이브리드 검색

[2026-07-28] turbovec(벡터 인덱스) + 매 쿼리 즉석 BM25Okapi 재구성 + 손으로 짠 RRF
조합을 Elasticsearch 하나로 교체하는 작업의 일부. 이 파일이 담당하는 것:
  - ES 커넥션 (지연 초기화, 연결 실패 시 None 반환 — turbovec가 없을 때와 동일한
    "있으면 쓰고 없으면 조용히 폴백" 패턴을 유지한다)
  - 인덱스 매핑 생성 (dense_vector 코사인 유사도 + BM25용 text 필드)
  - 청크 색인/삭제
  - 하이브리드 검색 (BM25 + kNN을 각각 ES에 질의한 뒤 파이썬에서 RRF로 합침)

[중요] ES의 네이티브 `retriever: rrf`는 쓰지 않는다 — 확인 결과 Enterprise 라이선스로
게이트돼 있어 무료(Basic) 자체 호스팅 배포에서는 라이선스 오류가 난다
(https://u11d.com/blog/reciprocal-rank-fusion-on-free-elasticsearch-licensing-workarounds-and-the-open-search-alternative/,
Elastic 공식 discuss 포럼에서도 동일 오류 재현됨). 그래서 BM25 질의와 kNN 질의를
각각 따로 던지고, main.py가 기존에 쓰던 것과 같은 RRF 공식을 파이썬에서 직접 적용한다
— 이 부분은 무료 티어에서도 라이선스 제약이 없다.

[알려진 제약] 한국어 형태소 분석기(nori 플러그인) 설치가 이 세션에서는 이 머신의
네트워크 문제(TLS bad record MAC, 큰 아티팩트 다운로드 시 반복 실패)로 아직 설치하지
못했다 — 지금은 표준(standard) 분석기로 동작한다. nori는 나중에 네트워크가 안정적일
때(또는 Oracle Cloud 배포 시) Dockerfile로 재시도할 것. standard 분석기는 공백/구두점
기준 토큰화라 "점주" 같은 짧은 한국어 단어의 형태소 인식은 kiwipiepy보다 정밀하지
않지만, BM25 자체는 정상 동작한다.
"""

import os

ES_URL = os.environ.get("ES_URL", "http://localhost:9200")
ES_INDEX = os.environ.get("ES_INDEX", "project_knowledge_hub_chunks")
EMBED_DIM = 384

_es_client = None
_index_ready = False


def get_es_client():
    """ES 클라이언트 지연 초기화. 연결 안 되면 None — 호출부는 turbovec 없을 때와
    동일하게 폴백(기존 방식 유지 또는 결과 없음)해야 한다."""
    global _es_client
    if _es_client is None:
        try:
            from elasticsearch import Elasticsearch
            client = Elasticsearch(ES_URL, request_timeout=5)
            if not client.ping():
                return None
            _es_client = client
        except Exception as e:
            print(f"[es] 연결 실패: {e}")
            return None
    return _es_client


def ensure_index():
    """인덱스가 없으면 매핑과 함께 생성. 이미 있으면 아무것도 안 함(idempotent)."""
    global _index_ready
    if _index_ready:
        return True
    client = get_es_client()
    if client is None:
        return False
    try:
        if client.indices.exists(index=ES_INDEX):
            _index_ready = True
            return True
        client.indices.create(
            index=ES_INDEX,
            mappings={
                "properties": {
                    "chunk_id":  {"type": "long"},
                    "doc_id":    {"type": "keyword"},
                    "filename":  {"type": "text"},
                    "title":     {"type": "text"},
                    "memo":      {"type": "text"},
                    "text":      {"type": "text"},
                    "page_num":  {"type": "integer"},
                    "chunk_idx": {"type": "integer"},
                    "embedding": {
                        "type": "dense_vector",
                        "dims": EMBED_DIM,
                        "index": True,
                        "similarity": "cosine",
                    },
                }
            },
        )
        _index_ready = True
        return True
    except Exception as e:
        print(f"[es] 인덱스 생성 실패: {e}")
        return False


def delete_doc_chunks(doc_id: int):
    """재인덱싱(재시도) 시 기존 청크 삭제 — DB의 old_ids 삭제와 짝을 맞춘다."""
    client = get_es_client()
    if client is None or not ensure_index():
        return
    try:
        client.delete_by_query(
            index=ES_INDEX,
            query={"term": {"doc_id": str(doc_id)}},
            conflicts="proceed",
            refresh=True,
        )
    except Exception as e:
        print(f"[es] doc_id={doc_id} 청크 삭제 실패: {e}")


def bulk_index_chunks(rows: list[dict]):
    """rows: [{chunk_id, doc_id, filename, title, memo, text, page_num, chunk_idx, embedding(list[float])}, ...]
    embedding이 없는 행(모델 로드 실패 등)은 건너뛴다 — BM25만으로도 검색은 가능해야 하므로
    text가 있으면 embedding 없이도 색인한다(그 경우 kNN에는 안 걸리고 BM25에만 걸림)."""
    client = get_es_client()
    if client is None or not ensure_index() or not rows:
        return
    from elasticsearch.helpers import bulk

    actions = []
    for r in rows:
        doc = {
            "chunk_id":  r["chunk_id"],
            "doc_id":    str(r["doc_id"]),
            "filename":  r.get("filename") or "",
            "title":     r.get("title") or "",
            "memo":      r.get("memo") or "",
            "text":      r["text"],
            "page_num":  r.get("page_num") or 0,
            "chunk_idx": r.get("chunk_idx") or 0,
        }
        if r.get("embedding") is not None:
            doc["embedding"] = r["embedding"]
        actions.append({
            "_index": ES_INDEX,
            "_id": str(r["chunk_id"]),
            "_source": doc,
        })
    try:
        bulk(client, actions, refresh=True)
    except Exception as e:
        print(f"[es] bulk 색인 실패: {e}")


def _doc_id_filter(doc_ids: list[int] | None):
    if not doc_ids:
        return None
    return {"terms": {"doc_id": [str(d) for d in doc_ids]}}


def bm25_search(query_text: str, doc_ids: list[int] | None, size: int = 50) -> list[dict]:
    """BM25(Lucene 기본) 텍스트 검색 — 전체 코퍼스 대상(즉석 재구성 불필요).
    반환: [{"chunk_id","doc_id","text","page_num","score"}, ...] score 내림차순."""
    client = get_es_client()
    if client is None or not ensure_index():
        return []
    must = [{"multi_match": {"query": query_text, "fields": ["text^2", "title", "filename", "memo"]}}]
    body = {"query": {"bool": {"must": must}}}
    doc_filter = _doc_id_filter(doc_ids)
    if doc_filter:
        body["query"]["bool"]["filter"] = [doc_filter]
    try:
        resp = client.search(index=ES_INDEX, size=size, **body)
    except Exception as e:
        print(f"[es] BM25 검색 실패: {e}")
        return []
    return [
        {
            "chunk_id": hit["_source"]["chunk_id"],
            "doc_id": int(hit["_source"]["doc_id"]),
            "text": hit["_source"]["text"],
            "page_num": hit["_source"]["page_num"],
            "score": hit["_score"],
        }
        for hit in resp["hits"]["hits"]
    ]


def knn_search(query_vector: list[float], doc_ids: list[int] | None, k: int = 200) -> list[dict]:
    """dense_vector 코사인 유사도 kNN 검색.
    반환: [{"chunk_id","doc_id","text","page_num","score"}, ...] score(코사인) 내림차순."""
    client = get_es_client()
    if client is None or not ensure_index():
        return []
    knn = {
        "field": "embedding",
        "query_vector": query_vector,
        "k": k,
        "num_candidates": max(k * 3, 100),
    }
    doc_filter = _doc_id_filter(doc_ids)
    if doc_filter:
        knn["filter"] = doc_filter
    try:
        resp = client.search(index=ES_INDEX, knn=knn, size=k)
    except Exception as e:
        print(f"[es] kNN 검색 실패: {e}")
        return []
    return [
        {
            "chunk_id": hit["_source"]["chunk_id"],
            "doc_id": int(hit["_source"]["doc_id"]),
            "text": hit["_source"]["text"],
            "page_num": hit["_source"]["page_num"],
            "score": hit["_score"],
        }
        for hit in resp["hits"]["hits"]
    ]


def hybrid_search(query_text: str, query_vector: list[float] | None, doc_ids: list[int] | None,
                   size: int = 50, k_sem: int = 30, k_bm25: int = 60) -> dict:
    """BM25 + kNN을 각각 질의한 뒤 RRF로 합친다 (main.py의 기존 K_SEM/K_BM25 공식과 동일).

    "점주" 버그의 2차 수정과 동일한 우선순위 로직도 여기서 유지한다: 코퍼스(필터된
    doc_ids 범위) 안에 실제 키워드 매치(BM25 score>0)가 하나라도 있으면, 키워드가 전혀
    없는 문서(순수 의미 유사도만 있는 문서)는 최종 후보에서 뺀다. 키워드가 전혀 없을 때만
    (모호한 패러프레이즈 검색) kNN 후보를 그대로 쓴다.

    반환: {"doc_ids": [...], "final_scores": {doc_id: score}, "best_chunk": {doc_id: {"text","page_num"}}}
    """
    bm25_hits = bm25_search(query_text, doc_ids, size=size) if query_text.strip() else []
    knn_hits = knn_search(query_vector, doc_ids, k=size) if query_vector is not None else []

    def _best_per_doc(hits):
        best: dict = {}
        for h in hits:
            did = h["doc_id"]
            if did not in best or h["score"] > best[did]["score"]:
                best[did] = h
        return best

    bm25_best = _best_per_doc(bm25_hits)
    sem_best  = _best_per_doc(knn_hits)

    has_keyword_match = len(bm25_best) > 0
    if has_keyword_match:
        candidate_ids = list(bm25_best.keys())
        # kNN에서만 잡힌 문서 중 BM25에도 걸린 문서가 있으면 그 정보(청크/페이지)도 씀
    else:
        candidate_ids = list(sem_best.keys())
    if not candidate_ids:
        return {"doc_ids": [], "final_scores": {}, "best_chunk": {}}

    sem_rank = {
        did: i + 1
        for i, did in enumerate(sorted(sem_best, key=lambda d: sem_best[d]["score"], reverse=True))
    }
    bm25_rank = {
        did: i + 1
        for i, did in enumerate(sorted(bm25_best, key=lambda d: bm25_best[d]["score"], reverse=True))
    }
    n = len(candidate_ids)
    final_scores = {}
    best_chunk = {}
    for did in candidate_ids:
        sr = sem_rank.get(did, n + 1)
        br = bm25_rank.get(did, n + 1)
        final_scores[did] = 1 / (k_sem + sr) + 1 / (k_bm25 + br)
        src = sem_best.get(did) or bm25_best.get(did)
        best_chunk[did] = {"text": src["text"], "page_num": src["page_num"]}

    return {"doc_ids": candidate_ids, "final_scores": final_scores, "best_chunk": best_chunk}

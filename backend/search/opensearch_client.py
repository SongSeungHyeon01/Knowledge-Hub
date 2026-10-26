"""opensearch_client.py — OpenSearch 연결 / 인덱스 관리 / 하이브리드 검색

[2026-10-24] Elasticsearch → OpenSearch 교체. 지금까지 쓰던 Elasticsearch 8.15 배포판의
라이선스는 Elastic License 2.0이라(이미지 안 /usr/share/elasticsearch/LICENSE.txt 직접 확인),
프로젝트 기준(상업 이용이 자유로운 허용형 라이선스만 사용)에 맞지 않았다. OpenSearch는
Apache-2.0이고(opensearchproject/opensearch:3.9.0 이미지 LICENSE.txt 확인), k-NN 플러그인이
기본 포함돼 있으며, 한국어 형태소 분석기 analysis-nori도 Apache-2.0 공식 플러그인으로 설치된다.
파이썬 클라이언트 opensearch-py도 Apache-2.0이다.

검색 동작은 es_client.py 시절과 같게 유지한다:
  - BM25 질의와 kNN 질의를 따로 던지고 파이썬에서 RRF로 합친다(벡터 k=30, BM25 k=60)
  - BM25는 collapse(doc_id)로 문서당 최상위 청크 하나만 후보로 쓴다
  - 키워드 매치가 하나라도 있으면 키워드 없는 순수 의미 유사도 문서는 뺀다("점주" 버그 2차 수정)
바뀐 것은 OpenSearch 문법뿐이다: dense_vector → knn_vector(Lucene HNSW, cosinesimil),
top-level knn → query.knn, 키워드 인자 → body.

연결이 안 되면 None을 돌려주고 호출부가 "검색 엔진 다운"으로 처리하는 패턴도 그대로다.
"""

import os

SEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
SEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "knowledge_hub_chunks")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "384"))

_client = None
_index_ready = False


def get_client():
    """OpenSearch 클라이언트 지연 초기화. 연결 안 되면 None."""
    global _client
    if _client is None:
        try:
            from opensearchpy import OpenSearch
            client = OpenSearch(SEARCH_URL, timeout=5)
            if not client.ping():
                return None
            _client = client
        except Exception as e:
            print(f"[search] 연결 실패: {e}")
            return None
    return _client


def ensure_index():
    """인덱스가 없으면 매핑과 함께 생성. 이미 있으면 아무것도 안 함(idempotent)."""
    global _index_ready
    if _index_ready:
        return True
    client = get_client()
    if client is None:
        return False
    try:
        if client.indices.exists(index=SEARCH_INDEX):
            _index_ready = True
            return True
        client.indices.create(index=SEARCH_INDEX, body={
            "settings": {
                "index": {"knn": True},
                # nori 플러그인은 토크나이저·필터만 제공하므로 analyzer를 직접 조립해 이름을 붙인다
                "analysis": {
                    "analyzer": {
                        "nori": {
                            "type": "custom",
                            "tokenizer": "nori_tokenizer",
                            "filter": ["nori_readingform", "lowercase"],
                        }
                    }
                },
            },
            "mappings": {
                "properties": {
                    "chunk_id":  {"type": "long"},
                    "doc_id":    {"type": "keyword"},
                    "filename":  {"type": "text", "analyzer": "nori"},
                    "title":     {"type": "text", "analyzer": "nori"},
                    "memo":      {"type": "text", "analyzer": "nori"},
                    "text":      {"type": "text", "analyzer": "nori"},
                    "page_num":  {"type": "integer"},
                    "chunk_idx": {"type": "integer"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": EMBED_DIM,
                        "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
                    },
                }
            },
        })
        _index_ready = True
        return True
    except Exception as e:
        print(f"[search] 인덱스 생성 실패: {e}")
        return False


def count_chunks() -> int | None:
    """색인된 청크 수. 연결이 안 되면 None."""
    client = get_client()
    if client is None or not ensure_index():
        return None
    try:
        return client.count(index=SEARCH_INDEX)["count"]
    except Exception as e:
        print(f"[search] count 실패: {e}")
        return None


def delete_doc_chunks(doc_id: int):
    """재인덱싱(재시도)·삭제 시 기존 청크 제거 — DB 청크 삭제와 짝을 맞춘다."""
    client = get_client()
    if client is None or not ensure_index():
        return
    try:
        client.delete_by_query(
            index=SEARCH_INDEX,
            body={"query": {"term": {"doc_id": str(doc_id)}}},
            params={"conflicts": "proceed", "refresh": "true"},
        )
    except Exception as e:
        print(f"[search] doc_id={doc_id} 청크 삭제 실패: {e}")


def bulk_index_chunks(rows: list[dict]) -> tuple[int, int]:
    """rows: [{chunk_id, doc_id, filename, title, memo, text, page_num, chunk_idx, embedding(list[float])}, ...]
    embedding이 없는 행도 text가 있으면 색인한다(BM25에만 걸림).
    반환: (성공 건수, 실패 건수). 연결·인덱스 실패도 (0, len(rows))로 보고한다."""
    client = get_client()
    if not rows:
        return (0, 0)
    if client is None or not ensure_index():
        print(f"[search] bulk 색인 불가(연결/인덱스 없음): {len(rows)}건 실패")
        return (0, len(rows))
    from opensearchpy.helpers import bulk

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
        actions.append({"_index": SEARCH_INDEX, "_id": str(r["chunk_id"]), "_source": doc})
    try:
        ok, errors = bulk(client, actions, refresh=True, raise_on_error=False, stats_only=False)
    except Exception as e:
        print(f"[search] bulk 색인 실패: {e}")
        return (0, len(actions))
    failed = len(errors)
    if failed:
        print(f"[search] bulk 색인 부분 실패: 성공 {ok}건 / 실패 {failed}건")
        for err in errors[:5]:
            print(f"[search]   실패 상세: {err}")
    return (ok, failed)


def _doc_id_filter(doc_ids: list[int] | None):
    if not doc_ids:
        return None
    return {"terms": {"doc_id": [str(d) for d in doc_ids]}}


# BM25 후보 예산 — collapse 덕분에 "문서 수"로 해석된다(청크 수가 아님).
BM25_CANDIDATE_DOCS = 100


def _hits(resp) -> list[dict]:
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


def bm25_search(query_text: str, doc_ids: list[int] | None, size: int = BM25_CANDIDATE_DOCS) -> list[dict]:
    """BM25 텍스트 검색. collapse(doc_id)로 문서당 최상위 청크 하나만 남긴다 — 파일명에
    검색어가 걸린 문서 하나가 모든 청크로 후보 예산을 독식하는 것을 막는다."""
    client = get_client()
    if client is None or not ensure_index():
        return []
    query = {"bool": {"must": [{"multi_match": {"query": query_text,
                                                "fields": ["text^2", "title", "filename", "memo"]}}]}}
    doc_filter = _doc_id_filter(doc_ids)
    if doc_filter:
        query["bool"]["filter"] = [doc_filter]
    try:
        resp = client.search(index=SEARCH_INDEX, body={"size": size, "query": query,
                                                       "collapse": {"field": "doc_id"}})
    except Exception as e:
        print(f"[search] BM25 검색 실패: {e}")
        return []
    return _hits(resp)


def knn_search(query_vector: list[float], doc_ids: list[int] | None, k: int = 200) -> list[dict]:
    """knn_vector 코사인 유사도 검색(Lucene HNSW). doc_ids 범위는 kNN 필터로 좁힌다."""
    client = get_client()
    if client is None or not ensure_index():
        return []
    knn = {"vector": query_vector, "k": k}
    doc_filter = _doc_id_filter(doc_ids)
    if doc_filter:
        knn["filter"] = doc_filter
    try:
        resp = client.search(index=SEARCH_INDEX, body={"size": k, "query": {"knn": {"embedding": knn}}})
    except Exception as e:
        print(f"[search] kNN 검색 실패: {e}")
        return []
    return _hits(resp)


def hybrid_search(query_text: str, query_vector: list[float] | None, doc_ids: list[int] | None,
                  size: int = 50, k_sem: int = 30, k_bm25: int = 60) -> dict:
    """BM25 + kNN을 각각 질의한 뒤 RRF로 합친다.

    코퍼스(필터된 doc_ids 범위) 안에 실제 키워드 매치(BM25)가 하나라도 있으면, 키워드가 전혀
    없는 문서(순수 의미 유사도만 있는 문서)는 최종 후보에서 뺀다. 키워드가 전혀 없을 때만
    kNN 후보를 그대로 쓴다.

    반환: {"doc_ids", "final_scores": {doc_id: score}, "best_chunk": {doc_id: {"text","page_num"}},
           "engine_down": bool} — engine_down=True면 결과가 원래 없는 게 아니라 검색 엔진에
    연결이 안 된 것이다(main.py가 응답에 실어 프론트가 구분해 보여준다)."""
    if get_client() is None:
        return {"doc_ids": [], "final_scores": {}, "best_chunk": {}, "engine_down": True}

    bm25_hits = (bm25_search(query_text, doc_ids, size=max(size, BM25_CANDIDATE_DOCS))
                 if query_text.strip() else [])
    knn_hits = knn_search(query_vector, doc_ids, k=size) if query_vector is not None else []

    def _best_per_doc(hits):
        best: dict = {}
        for h in hits:
            did = h["doc_id"]
            if did not in best or h["score"] > best[did]["score"]:
                best[did] = h
        return best

    bm25_best = _best_per_doc(bm25_hits)
    sem_best = _best_per_doc(knn_hits)

    candidate_ids = list(bm25_best.keys()) if bm25_best else list(sem_best.keys())
    if not candidate_ids:
        return {"doc_ids": [], "final_scores": {}, "best_chunk": {}, "engine_down": False}

    sem_rank = {did: i + 1 for i, did in
                enumerate(sorted(sem_best, key=lambda d: sem_best[d]["score"], reverse=True))}
    bm25_rank = {did: i + 1 for i, did in
                 enumerate(sorted(bm25_best, key=lambda d: bm25_best[d]["score"], reverse=True))}
    n = len(candidate_ids)
    final_scores, best_chunk = {}, {}
    for did in candidate_ids:
        sr = sem_rank.get(did, n + 1)
        br = bm25_rank.get(did, n + 1)
        final_scores[did] = 1 / (k_sem + sr) + 1 / (k_bm25 + br)
        src = sem_best.get(did) or bm25_best.get(did)
        best_chunk[did] = {"text": src["text"], "page_num": src["page_num"]}

    return {"doc_ids": candidate_ids, "final_scores": final_scores, "best_chunk": best_chunk,
            "engine_down": False}

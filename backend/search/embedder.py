"""embedder.py — 임베딩 모델 로딩을 한 곳에서 관리한다.

서버(main.py)와 재색인 스크립트(scripts/search_backfill.py)가 반드시 같은 모델을 쓰도록
모델 이름과 차원을 여기서만 정한다. 모델은 EMBED_MODEL 환경변수로 바꿀 수 있고,
검색 인덱스의 벡터 차원(opensearch_client.EMBED_DIM)과 다르면 시작할 때 바로 알린다 —
차원이 다른 벡터는 색인 단계에서 전부 거부되는데, 그 실패가 조용히 묻히지 않게 하려는 것.
"""

import os

EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

# 질문/문서 앞에 붙여야 하는 접두어 — 각 모델 카드에 적힌 사용법 그대로(2026-10-24 확인).
# 표에 없는 모델은 EMBED_QUERY_PREFIX / EMBED_PASSAGE_PREFIX 환경변수로 지정한다.
_KNOWN_PREFIXES = {
    "intfloat/multilingual-e5-small": ("query: ", "passage: "),
    "intfloat/multilingual-e5-base": ("query: ", "passage: "),
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "dragonkue/snowflake-arctic-embed-l-v2.0-ko": ("query: ", ""),
}
QUERY_PREFIX = os.environ.get("EMBED_QUERY_PREFIX", _KNOWN_PREFIXES.get(EMBED_MODEL, ("", ""))[0])
PASSAGE_PREFIX = os.environ.get("EMBED_PASSAGE_PREFIX", _KNOWN_PREFIXES.get(EMBED_MODEL, ("", ""))[1])

_model = None
_load_failed = False


def get_model():
    """지연 로딩. 실패하면 None — 호출부는 임베딩 없이(BM25만으로) 동작해야 한다."""
    global _model, _load_failed
    if _model is None and not _load_failed:
        try:
            from sentence_transformers import SentenceTransformer
            print(f"[embed] {EMBED_MODEL} 로딩 중 (첫 실행 시 모델 다운로드)...")
            model = SentenceTransformer(EMBED_MODEL)
            from search.opensearch_client import EMBED_DIM
            dim = model.get_sentence_embedding_dimension()
            if dim != EMBED_DIM:
                print(f"[embed] 모델 차원 {dim}이 검색 인덱스 차원 EMBED_DIM={EMBED_DIM}과 다릅니다 — "
                      f"EMBED_DIM 환경변수를 {dim}으로 맞추고 인덱스를 새로 만드세요. 임베딩을 끕니다.")
                _load_failed = True
                return None
            _model = model
            print(f"[embed] 로딩 완료 (dim={dim}, max_seq_length={model.max_seq_length})")
        except Exception as e:
            print(f"[embed] 로드 실패: {e}")
            _load_failed = True
    return _model


def _encode(texts: list[str], batch_size: int = 32):
    model = get_model()
    if model is None:
        return None
    import numpy as np
    vecs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


def encode_passages(texts: list[str]):
    """색인할 문서 청크 → 정규화된 float32 벡터 배열. 모델이 없으면 None."""
    return _encode([PASSAGE_PREFIX + t for t in texts])


def encode_queries(texts: list[str]):
    """검색 질문 → 정규화된 float32 벡터 배열. 모델이 없으면 None."""
    return _encode([QUERY_PREFIX + t for t in texts])

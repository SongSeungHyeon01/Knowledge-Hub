"""embedder.py — 임베딩 모델 로딩을 한 곳에서 관리한다.

서버(main.py)와 재색인 스크립트(scripts/search_backfill.py)가 반드시 같은 모델을 쓰도록
모델 이름과 차원을 여기서만 정한다. 모델은 EMBED_MODEL 환경변수로 바꿀 수 있고,
검색 인덱스의 벡터 차원(opensearch_client.EMBED_DIM)과 다르면 시작할 때 바로 알린다 —
차원이 다른 벡터는 색인 단계에서 전부 거부되는데, 그 실패가 조용히 묻히지 않게 하려는 것.

[2026-10-25] 기본 모델을 paraphrase-multilingual-MiniLM-L12-v2 → nlpai-lab/KURE-v1(MIT, 1024차원)로
바꿨다. 질문 47개 측정(eval/run_eval.py, 같은 파싱 결과에서 모델만 교체)에서:
  MiniLM   hit@1 61.7% / hit@5  93.6% / MRR 0.721 / 청크 1,512개 임베딩 42초
  e5-small hit@1 78.7% / hit@5  93.6% / MRR 0.859 / 109초
  KURE-v1  hit@1 89.4% / hit@5 100.0% / MRR 0.941 / 471초 (검색 1회 평균 0.18초)
MiniLM은 입력을 128토큰에서 자르는데 우리 청크(400자)는 134~218토큰이라 뒷부분이 버려졌다.
KURE-v1은 8192토큰까지 받는다. 가벼운 구성이 필요하면 EMBED_MODEL=intfloat/multilingual-e5-small,
EMBED_DIM=384로 바꾸고 scripts/search_backfill.py --reembed로 다시 색인한다.
"""

import os
import threading

EMBED_MODEL = os.environ.get("EMBED_MODEL", "nlpai-lab/KURE-v1")

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
_load_lock = threading.Lock()   # 시작 시 미리 올리기와 첫 검색이 겹쳐도 모델을 한 번만 올린다


def get_model():
    """지연 로딩. 실패하면 None — 호출부는 임베딩 없이(BM25만으로) 동작해야 한다."""
    with _load_lock:
        return _load_model()


def _load_model():
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

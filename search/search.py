"""
search.py — 청킹 + 임베딩 + BM25 + 하이브리드 검색 통합 모듈
==============================================================
담당 : 김태훈 ③  (임베딩 + 하이브리드 검색엔진 + 청킹)
연결 : 윤준서① parse_pdf() 출력 → ingest(parse_result, searcher)
       HybridSearcher.search(query) → 김기빈④ Backend API

공개 API
--------
searcher = HybridSearcher()
ingest(parse_result, searcher)          # 문서 인덱싱
results = searcher.search(query, top_k) # 하이브리드 검색
searcher.delete_document(doc_id)        # 문서 삭제
"""

import json
import pickle
import re
import numpy as np
from pathlib import Path

from sentence_transformers import SentenceTransformer
from kiwipiepy import Kiwi
from rank_bm25 import BM25Okapi
import turbovec


# ── 경로 / 전역 상수 ─────────────────────────────────────────────────────────

_DATA_DIR   = Path("data")
_INDEX_PATH = _DATA_DIR / "index.tvim"
_VEC_META   = _DATA_DIR / "vec_meta.json"
_BM25_PATH  = _DATA_DIR / "bm25.pkl"
_BM25_META  = _DATA_DIR / "bm25_meta.json"

_MODEL_NAME  = "paraphrase-multilingual-MiniLM-L12-v2"
_LOCAL_MODEL = Path(__file__).parent / "models" / _MODEL_NAME  # 폐쇄망 로컬 경로

_RRF_K = 60  # Reciprocal Rank Fusion 상수 (관례: 60)


# ════════════════════════════════════════════════════════════════════════════
# 1. 청킹 (Chunker)
# ════════════════════════════════════════════════════════════════════════════

def chunk_text(doc_id: str, text: str, chunk_size: int = 300, overlap: int = 50) -> list[dict]:
    """
    텍스트를 chunk_size(기본 300자) 단위로 분할, overlap(50자)만큼 앞 청크와 겹침.

    자르기 우선순위: 빈 줄 > 줄바꿈 > 영/한 문장 종결 부호 > 강제 자르기
    반환: [{"chunk_id", "doc_id", "text", "char_start", "char_end"}, ...]
    """
    if not text or not text.strip():
        return []

    chunks = []
    start  = 0
    idx    = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            piece = text[start:].strip()
            if piece and (not chunks or len(piece) >= overlap):
                chunks.append(_make_chunk(doc_id, idx, piece, start, len(text)))
            break

        cut   = _find_cut(text, start, end)
        piece = text[start:cut].strip()
        if piece:
            chunks.append(_make_chunk(doc_id, idx, piece, start, cut))
            idx += 1

        next_start = cut - overlap
        start = next_start if next_start > start else cut

    return chunks


def _find_cut(text: str, start: int, end: int) -> int:
    """end 직전 100자 안에서 자연 경계를 뒤에서부터 탐색."""
    window = max(start + 1, end - 100)
    for sep in ['\n\n', '\n', '. ', '? ', '! ', '。', '？', '！']:
        pos = text.rfind(sep, window, end)
        if pos != -1:
            return pos + len(sep)
    return end


def _make_chunk(doc_id: str, idx: int, text: str, char_start: int, char_end: int) -> dict:
    return {
        "chunk_id":   f"{doc_id}_chunk_{idx:03d}",
        "doc_id":     doc_id,
        "text":       text,
        "char_start": char_start,
        "char_end":   char_end,
    }


# ════════════════════════════════════════════════════════════════════════════
# 2. 임베딩 (Embedder)
# ════════════════════════════════════════════════════════════════════════════

class Embedder:
    """
    paraphrase-multilingual-MiniLM-L12-v2 기반 384차원 다국어 임베딩.
    로컬 models/ 폴더가 있으면 오프라인 로딩 (폐쇄망 대응).
    """

    def __init__(self):
        src = str(_LOCAL_MODEL) if _LOCAL_MODEL.exists() else _MODEL_NAME
        print(f"[임베딩] 모델 로딩: {src}")
        self.model = SentenceTransformer(src)
        print("[임베딩] 로딩 완료")

    def embed(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        """텍스트 리스트 → (N, 384) float32 배열 (L2 정규화)."""
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        cleaned = [t if t and t.strip() else " " for t in texts]
        vectors = self.model.encode(
            cleaned,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return vectors.astype(np.float32)

    def embed_one(self, text: str) -> np.ndarray:
        """텍스트 1개 → (384,) 벡터 (검색 쿼리용)."""
        if not text or not text.strip():
            raise ValueError("검색 쿼리가 비어 있습니다.")
        return self.embed([text])[0]


# ════════════════════════════════════════════════════════════════════════════
# 3. 벡터 저장소 (VectorStore)
# ════════════════════════════════════════════════════════════════════════════

class VectorStore:
    """
    turbovec IdMapIndex 기반 벡터 저장·검색.
    data/index.tvim + data/vec_meta.json 으로 영속화.
    """

    def __init__(self):
        if _INDEX_PATH.exists() and _VEC_META.exists():
            self._load()
        else:
            self.index      = turbovec.IdMapIndex(dim=384)
            self.metadata   = {}   # {str(uint_id): {chunk_id, doc_id, text}}
            self.id_counter = 0

    def add_batch(self, chunks: list[dict], vectors: np.ndarray):
        """청크 + 벡터 배치 추가."""
        if not chunks:
            return
        n   = len(chunks)
        ids = np.arange(self.id_counter, self.id_counter + n, dtype=np.uint64)
        self.index.add_with_ids(vectors.astype(np.float32), ids)
        for i, c in enumerate(chunks):
            self.metadata[str(self.id_counter + i)] = {
                "chunk_id": c["chunk_id"],
                "doc_id":   c["doc_id"],
                "text":     c["text"],
            }
        self.id_counter += n
        self._save()

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> list[dict]:
        """유사 벡터 검색. score 내림차순 반환."""
        if not self.metadata:
            return []
        actual_k    = min(top_k, len(self.metadata))
        queries     = query_vector.reshape(1, -1).astype(np.float32)
        scores, ids = self.index.search(queries, actual_k)
        return [
            {**self.metadata[str(uid)], "score": float(s)}
            for s, uid in zip(scores[0], ids[0])
            if str(uid) in self.metadata
        ]

    def delete_document(self, doc_id: str):
        """doc_id에 해당하는 청크 전체 삭제."""
        targets = [uid for uid, m in self.metadata.items() if m["doc_id"] == doc_id]
        for uid in targets:
            self.index.remove(int(uid))
            del self.metadata[uid]
        self._save()
        print(f"[VectorStore] doc_id='{doc_id}' 청크 {len(targets)}개 삭제")

    def _save(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.index.write(str(_INDEX_PATH))
        _VEC_META.write_text(
            json.dumps({"metadata": self.metadata, "id_counter": self.id_counter},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self):
        self.index      = turbovec.IdMapIndex.load(str(_INDEX_PATH))
        data            = json.loads(_VEC_META.read_text(encoding="utf-8"))
        self.metadata   = data["metadata"]
        self.id_counter = data["id_counter"]
        print(f"[VectorStore] 기존 인덱스 복원: {len(self.metadata)}개 청크")


# ════════════════════════════════════════════════════════════════════════════
# 4. BM25 색인 (BM25Indexer)
# ════════════════════════════════════════════════════════════════════════════

# 한국어 형태소 분석에서 보존할 품사
_KEEP_TAGS = {"NNG", "NNP", "VV", "VA", "SL"}

# 영어 불용어
_EN_STOP_WORDS = {
    "a","an","the","is","are","was","were","be","been","being",
    "have","has","had","do","does","did","will","would","shall",
    "should","may","might","must","can","could","to","of","in",
    "for","on","with","at","by","from","up","about","into","through",
    "and","or","but","if","as","this","that","these","those","it",
    "its","not","no","only","so","than","too","very","just","when",
    "where","which","who","all","each","both","more","most","other",
    "such","then","how","what","they","them","their","we","our","you",
    "your","he","she","him","her","i","me","my","any","also","use",
    "used","using","after","before","returns","following","without",
    "one","two","three","example","examples","see","note","notes",
}


class BM25Indexer:
    """
    rank-bm25 + kiwipiepy 기반 BM25 키워드 색인.
    언어 자동 감지 후 영어/한국어 토크나이저 분기.
    data/bm25.pkl + data/bm25_meta.json 으로 영속화.
    """

    def __init__(self):
        self.kiwi     = Kiwi()
        self.corpus   = []   # List[List[str]]
        self.metadata = []   # List[{chunk_id, doc_id, text}]
        self.bm25     = None
        if _BM25_PATH.exists() and _BM25_META.exists():
            self._load()

    # ── 공개 메서드 ──────────────────────────────────────────────────────

    def add_batch(self, chunks: list[dict]):
        if not chunks:
            return
        for c in chunks:
            self.corpus.append(self._tokenize(c["text"]))
            self.metadata.append({"chunk_id": c["chunk_id"],
                                   "doc_id": c["doc_id"], "text": c["text"]})
        self._rebuild()
        self._save()

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        if not self.bm25 or not query or not query.strip():
            return []
        tokens = self._tokenize_query(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [{**self.metadata[i], "score": float(s)} for i, s in ranked[:top_k]]

    def delete_document(self, doc_id: str):
        pairs = [(c, m) for c, m in zip(self.corpus, self.metadata)
                 if m["doc_id"] != doc_id]
        removed = len(self.metadata) - len(pairs)
        if pairs:
            self.corpus, self.metadata = map(list, zip(*pairs))
        else:
            self.corpus, self.metadata = [], []
        self._rebuild()
        self._save()
        print(f"[BM25] doc_id='{doc_id}' 청크 {removed}개 삭제")

    # ── 내부 메서드 ──────────────────────────────────────────────────────

    def _detect_language(self, text: str) -> str:
        """ASCII 알파벳 비율 > 70% → 영어, 이하 → 한국어."""
        alpha = [c for c in text if c.isalpha()]
        if not alpha:
            return "ko"
        return "en" if sum(1 for c in alpha if c.isascii()) / len(alpha) > 0.7 else "ko"

    def _tokenize_en(self, text: str) -> list[str]:
        """영어 토큰화: 소문자 → 알파벳+숫자 추출 → 불용어/단글자 제거."""
        tokens = re.findall(r'[a-zA-Z][a-zA-Z0-9_]*', text.lower())
        result = [t for t in tokens if len(t) >= 2 and t not in _EN_STOP_WORDS]
        return result if result else text.split()

    def _tokenize(self, text: str) -> list[str]:
        """문서 색인용: 언어 감지 후 적합한 토크나이저 적용."""
        if self._detect_language(text) == "en":
            return self._tokenize_en(text)
        ko_tokens = [t.form for t in self.kiwi.tokenize(text)
                     if t.tag in _KEEP_TAGS and len(t.form) >= 2]
        en_extras = self._tokenize_en(text)
        combined  = ko_tokens + [t for t in en_extras if t not in ko_tokens]
        return combined if combined else text.split()

    def _tokenize_query(self, text: str) -> list[str]:
        """쿼리 전용 토큰화: 한국어 문자가 포함되면 한/영 토큰 동시 추출.
        EN→KO 쿼리 보강 후 한/영 혼합 문자열에 대응.
        """
        if not any('가' <= c <= '힣' for c in text):
            return self._tokenize(text)
        ko_tokens = [t.form for t in self.kiwi.tokenize(text)
                     if t.tag in _KEEP_TAGS and len(t.form) >= 2]
        en_extras = self._tokenize_en(text)
        combined  = ko_tokens + [t for t in en_extras if t not in ko_tokens]
        return combined if combined else text.split()

    def _rebuild(self):
        self.bm25 = BM25Okapi(self.corpus) if self.corpus else None

    def _save(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_BM25_PATH, "wb") as f:
            pickle.dump(self.corpus, f)
        _BM25_META.write_text(
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self):
        with open(_BM25_PATH, "rb") as f:
            self.corpus = pickle.load(f)
        self.metadata = json.loads(_BM25_META.read_text(encoding="utf-8"))
        self._rebuild()
        print(f"[BM25] 기존 인덱스 복원: {len(self.metadata)}개 청크")


# ════════════════════════════════════════════════════════════════════════════
# 5. 하이브리드 검색 / RRF (HybridSearcher)
# ════════════════════════════════════════════════════════════════════════════

# 반도체/FPGA 도메인 영어→한국어 기술 용어 매핑
# 포함 기준: 도메인 특정 용어 (코퍼스 내 IDF 높음)
# 제외 기준: interface/signal/clock처럼 모든 문서에 등장하는 범용 단어
#            → 추가 시 한국어 문서 전체를 무차별 부스팅해 역효과
_EN_TO_KO_TECH: dict[str, str] = {
    "oscilloscope":     "오실로스코프",
    "timing margin":    "타이밍 마진",
    "margin":           "마진",
    "measurement":      "측정",
    "erase":            "소거",
    "bad block":        "배드 블록",
    "endurance":        "내구성",
    "temperature":      "온도",
    "high temperature": "고온",
    "low temperature":  "저온",
    "synthesis":        "합성",
    "utilization":      "사용률",
    "simulation":       "시뮬레이션",
}


def _augment_en_to_ko(query: str) -> str:
    """영어 쿼리에 한국어 기술 동의어를 추가해 한국어 문서 BM25 매칭 지원.
    이미 한국어 문자가 있거나 매핑되는 용어가 없으면 원본 반환.
    """
    if any('가' <= c <= '힣' for c in query):
        return query
    q_lower = query.lower()
    extras: list[str] = []
    for en_term in sorted(_EN_TO_KO_TECH, key=len, reverse=True):
        if en_term in q_lower:
            ko = _EN_TO_KO_TECH[en_term]
            if ko not in extras:
                extras.append(ko)
    return (query + " " + " ".join(extras)) if extras else query


class HybridSearcher:
    """
    BM25 + 벡터 검색을 RRF로 합산하는 하이브리드 검색 엔진.

    score = 1/(60 + bm25_rank) + 1/(60 + vec_rank)

    주요 메서드
    -----------
    index_document(doc_id, chunks)  문서 인덱싱 (중복 시 자동 upsert)
    search(query, top_k)            하이브리드 검색
    delete_document(doc_id)         문서 삭제
    """

    def __init__(self):
        self.embedder  = Embedder()
        self.vec_store = VectorStore()
        self.bm25      = BM25Indexer()

    def index_document(self, doc_id: str, chunks: list[dict]):
        if not chunks:
            print(f"[HybridSearcher] '{doc_id}' 청크 없음 — 인덱싱 건너뜀")
            return
        if self._exists(doc_id):
            print(f"[HybridSearcher] '{doc_id}' 이미 존재 — 기존 데이터 삭제 후 재인덱싱")
            self.delete_document(doc_id)
        texts   = [c["text"] for c in chunks]
        vectors = self.embedder.embed(texts)
        self.vec_store.add_batch(chunks, vectors)
        self.bm25.add_batch(chunks)
        print(f"[HybridSearcher] '{doc_id}' 청크 {len(chunks)}개 인덱싱 완료")

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """
        하이브리드 검색 결과 반환.
        반환 형식: [{"chunk_id", "doc_id", "text", "score", "bm25_rank", "vec_rank"}, ...]
        """
        if not query or not query.strip():
            raise ValueError("검색 쿼리가 비어 있습니다.")

        total   = len(self.vec_store.metadata)
        fetch_k = min(max(top_k * 3, 30), total) if total > 0 else top_k

        # EN→KO 쿼리 보강: BM25에만 적용, 벡터는 원본 쿼리 사용
        bm25_query   = _augment_en_to_ko(query)
        bm25_results = self.bm25.search(bm25_query, top_k=fetch_k)
        query_vec    = self.embedder.embed_one(query)
        vec_results  = self.vec_store.search(query_vec, top_k=fetch_k)

        rrf_scores: dict[str, dict] = {}

        for rank, r in enumerate(bm25_results):
            cid = r["chunk_id"]
            if cid not in rrf_scores:
                rrf_scores[cid] = {"chunk_id": cid, "doc_id": r["doc_id"],
                                   "text": r["text"], "score": 0.0,
                                   "bm25_rank": None, "vec_rank": None}
            rrf_scores[cid]["score"]    += 1 / (_RRF_K + rank + 1)
            rrf_scores[cid]["bm25_rank"] = rank + 1

        for rank, r in enumerate(vec_results):
            cid = r["chunk_id"]
            if cid not in rrf_scores:
                rrf_scores[cid] = {"chunk_id": cid, "doc_id": r["doc_id"],
                                   "text": r["text"], "score": 0.0,
                                   "bm25_rank": None, "vec_rank": None}
            rrf_scores[cid]["score"]   += 1 / (_RRF_K + rank + 1)
            rrf_scores[cid]["vec_rank"] = rank + 1

        return sorted(rrf_scores.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    def delete_document(self, doc_id: str):
        """벡터 DB + BM25 양쪽에서 문서 삭제."""
        self.vec_store.delete_document(doc_id)
        self.bm25.delete_document(doc_id)

    def _exists(self, doc_id: str) -> bool:
        return any(m["doc_id"] == doc_id for m in self.vec_store.metadata.values())


# ════════════════════════════════════════════════════════════════════════════
# 6. 파이프라인 (윤준서①↔김태훈③ 연결)
# ════════════════════════════════════════════════════════════════════════════

def ingest(parse_result: dict, searcher: HybridSearcher) -> dict:
    """
    윤준서① parse_pdf() 결과를 받아 청킹 + 인덱싱까지 처리.

    Parameters
    ----------
    parse_result : parse_pdf() 반환값
        {source_file, status, error, pages: [{page, text, flagged, ...}]}
    searcher : HybridSearcher 인스턴스

    Returns
    -------
    {
        "doc_id":        str | None,
        "status":        "success" | "skipped" | "failed",
        "error":         None | str,
        "total_pages":   int,
        "indexed_pages": int,
        "flagged_pages": list[int],
        "chunk_count":   int,
    }
    """
    if parse_result["status"] == "failed":
        return {"doc_id": None, "status": "failed",
                "error": parse_result["error"],
                "total_pages": 0, "indexed_pages": 0,
                "flagged_pages": [], "chunk_count": 0}

    doc_id        = Path(parse_result["source_file"]).stem
    flagged_pages = []
    texts         = []

    for p in parse_result["pages"]:
        if p["flagged"]:
            flagged_pages.append(p["page"])
            print(f"  [경고] '{doc_id}' {p['page']}페이지 — OCR 신뢰도 낮음, 건너뜀")
            continue
        if not p["text"] or not p["text"].strip():
            continue
        texts.append(p["text"])

    if not texts:
        return {"doc_id": doc_id, "status": "skipped",
                "error": "모든 페이지의 OCR 신뢰도가 낮아 인덱싱 불가",
                "total_pages": len(parse_result["pages"]),
                "indexed_pages": 0, "flagged_pages": flagged_pages, "chunk_count": 0}

    chunks = chunk_text(doc_id, "\n".join(texts))

    if not chunks:
        return {"doc_id": doc_id, "status": "skipped",
                "error": "텍스트 추출 후 청킹 결과가 없습니다.",
                "total_pages": len(parse_result["pages"]),
                "indexed_pages": 0, "flagged_pages": flagged_pages, "chunk_count": 0}

    searcher.index_document(doc_id, chunks)

    return {"doc_id": doc_id, "status": "success", "error": None,
            "total_pages": len(parse_result["pages"]),
            "indexed_pages": len(texts),
            "flagged_pages": flagged_pages,
            "chunk_count": len(chunks)}

"""
search.py — 청킹 + BM25 토크나이저 모듈
==============================================================
main.py가 실제로 쓰는 건 이 파일의 두 가지뿐이다:
  - chunk_text()   : 섹션 헤더 우선 청킹 (문서 인덱싱 시 사용)
  - BM25Indexer     : 언어감지 토크나이저(_tokenize/_tokenize_query) — 유사 검색
                       후보 안에서 매 쿼리마다 즉석으로 BM25 재랭킹할 때 사용

[정리 2026-07-25] 이 파일은 원래 임베딩·벡터저장소·하이브리드 검색엔진까지 포함한
독립 모듈(HybridSearcher 등)로 작성됐었다. 하지만 main.py는 자체 turbovec
인덱스·임베딩 로더·검색/RRF 로직을 따로 구현해 쓰고 있어(환경 독립 아키텍처·
PostgreSQL 저장 등 요구사항이 달랐음), 이 파일의 Embedder/VectorStore/
HybridSearcher/ingest()와 한↔영 동의어 보강 로직은 한 번도 호출되지 않는 죽은
코드였다 — 실제 사용처(chunk_text, BM25Indexer)만 남기고 정리했다.
"""

import json
import os
import pickle
import re
from pathlib import Path

from kiwipiepy import Kiwi
from rank_bm25 import BM25Okapi


# ── 경로 / 전역 상수 ─────────────────────────────────────────────────────────

# [수정 2026-07-06] cwd 기준 상대경로("data")였던 것을 main.py:38의 DATA_DIR 규칙과
# 동일하게 통일한다 — main.py:38과 동일 규칙이므로 반드시 함께 변경할 것.
# DATA_DIR 환경변수 우선, 없으면 backend/ 디렉토리(이 파일의 부모의 부모) 기준
# 절대경로로 폴백 — 실행 시점 작업 디렉토리(cwd)에 좌우되지 않게 하기 위함.
# 환경변수만 바꿔 배포 위치를 이식할 수 있어야 한다는 방침(환경 독립적 아키텍처) 때문에,
# main.py가 보는 uploads/parsed와 이 검색 인덱스가 서로 다른 위치를 보는 일이 없어야 한다.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/search/ → backend/
_DATA_DIR   = Path(os.environ.get("DATA_DIR", os.path.join(_BACKEND_DIR, "data")))
_BM25_PATH  = _DATA_DIR / "bm25.pkl"
_BM25_META  = _DATA_DIR / "bm25_meta.json"

# 청킹 상수 (PRD 4차 수정 기준: 512토큰 / 50토큰 오버랩)
_MAX_CHUNK_CHARS = 400  # ≈ 270 tokens (한/영 평균 약 1.5자/토큰, 512토큰 이내 안전 마진)
_OVERLAP_CHARS   = 80   # ≈ 50 tokens

# 섹션 헤더 패턴: 마크다운 헤더 / 숫자 목차 / 한국어 장·절
_SECTION_RE = re.compile(
    r'^(?:#{1,4}\s+.+|\d+(?:\.\d+)*\.\s+\S.{0,60}|제\s*\d+\s*[장절항].{0,40})',
    re.MULTILINE,
)


# ════════════════════════════════════════════════════════════════════════════
# 1. 청킹 (Chunker)
# ════════════════════════════════════════════════════════════════════════════

def chunk_text(doc_id: str, text: str) -> list[dict]:
    """
    섹션 헤더 우선 청킹 (PRD 1순위).
    헤더가 2개 미만이거나 섹션이 _MAX_CHUNK_CHARS 초과 시 고정 크기 분할 (PRD 2순위).
    반환: [{"chunk_id", "doc_id", "text", "char_start", "char_end"}, ...]
    """
    if not text or not text.strip():
        return []

    sections = _split_by_sections(text)
    chunks: list[dict] = []
    idx = 0

    for sec_text, sec_offset in sections:
        sec_text = sec_text.strip()
        if not sec_text:
            continue
        if len(sec_text) <= _MAX_CHUNK_CHARS:
            chunks.append(_make_chunk(doc_id, idx, sec_text,
                                      sec_offset, sec_offset + len(sec_text)))
            idx += 1
        else:
            sub = _split_fixed(doc_id, sec_text, idx, sec_offset)
            chunks.extend(sub)
            idx += len(sub)

    return chunks


def _split_by_sections(text: str) -> list[tuple[str, int]]:
    """섹션 헤더 패턴으로 분할. 헤더가 2개 미만이면 [(text, 0)] 반환."""
    matches = list(_SECTION_RE.finditer(text))
    if len(matches) < 2:
        return [(text, 0)]
    sections = []
    for i, m in enumerate(matches):
        start = m.start()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((text[start:end], start))
    return sections


def _split_fixed(doc_id: str, text: str, start_idx: int, base_offset: int = 0) -> list[dict]:
    """_MAX_CHUNK_CHARS 단위 고정 크기 분할 (섹션 없거나 섹션이 초과 크기인 경우)."""
    chunks: list[dict] = []
    start = 0
    idx   = start_idx

    while start < len(text):
        end = start + _MAX_CHUNK_CHARS

        if end >= len(text):
            piece = text[start:].strip()
            if piece and (not chunks or len(piece) >= _OVERLAP_CHARS):
                chunks.append(_make_chunk(doc_id, idx, piece,
                                          base_offset + start, base_offset + len(text)))
                idx += 1
            break

        cut   = _find_cut(text, start, end)
        piece = text[start:cut].strip()
        if piece:
            chunks.append(_make_chunk(doc_id, idx, piece,
                                      base_offset + start, base_offset + cut))
            idx += 1

        next_start = cut - _OVERLAP_CHARS
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
# 2. BM25 색인 (BM25Indexer)
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

    def add_batch(self, chunks: list[dict]) -> list[list[str]]:
        """청크 배치 추가. 각 청크의 tokenized_text 리스트를 반환 (PostgreSQL 저장용)."""
        if not chunks:
            return []
        tokenized = []
        for c in chunks:
            tokens = self._tokenize(c["text"])
            self.corpus.append(tokens)
            self.metadata.append({"chunk_id": c["chunk_id"],
                                   "doc_id": c["doc_id"], "text": c["text"]})
            tokenized.append(tokens)
        self._rebuild()
        self._save()
        return tokenized

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        if not self.bm25 or not query or not query.strip():
            return []
        tokens = self._tokenize_query(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(((i, s) for i, s in enumerate(scores) if s > 0),
                        key=lambda x: x[1], reverse=True)
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
        """영어 토큰화: 소문자 → 알파벳+숫자 추출 → 불용어/단글자 제거.
        코드·스펙 쿼리 대응: 숫자+단위(1.8V, 200MHz), 16진수(0x60) 추가 추출.
        """
        t = text.lower()
        tokens   = re.findall(r'[a-zA-Z][a-zA-Z0-9_]*', t)          # 일반 영문 토큰
        num_unit = re.findall(r'\d+\.?\d*[a-z]+', t)                 # 1.8v, 200mhz, 512mb
        hex_vals = re.findall(r'0x[0-9a-f]+', t)                     # 0x60, 0xff
        seen, result = set(), []
        for tok in tokens + num_unit + hex_vals:
            if len(tok) >= 2 and tok not in _EN_STOP_WORDS and tok not in seen:
                seen.add(tok)
                result.append(tok)
        return result if result else text.split()

    def _tokenize(self, text: str) -> list[str]:
        """문서 색인용: 언어 감지 후 적합한 토크나이저 적용."""
        if self._detect_language(text) == "en":
            return self._tokenize_en(text)
        ko_tokens = [t.form.lower() if t.tag == "SL" else t.form
                     for t in self.kiwi.tokenize(text)
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
        ko_tokens = [t.form.lower() if t.tag == "SL" else t.form
                     for t in self.kiwi.tokenize(text)
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

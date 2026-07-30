"""
search.py — 청킹 모듈
==============================================================
main.py가 이 파일에서 쓰는 건 chunk_text() 하나다(섹션 헤더 우선 청킹, 문서 인덱싱 시 사용).

[정리 2026-07-30] rank-bm25 + kiwipiepy 기반 BM25Indexer를 삭제했다. Elasticsearch
마이그레이션(2026-07-28)으로 main.py의 호출부(_get_search_indexer)가 제거된 뒤 호출부가
전혀 없는 죽은 코드였고, 그 상태로 rank-bm25/kiwipiepy 의존성만 붙잡고 있었다. BM25는
이제 ES 내장 Lucene BM25(+ standard 분석기, nori 플러그인은 아직 미설치)가 담당한다.

[정리 2026-07-25] 이 파일은 원래 임베딩·벡터저장소·하이브리드 검색엔진까지 포함한
독립 모듈(HybridSearcher 등)로 작성됐었다. 하지만 main.py는 자체 인덱스·임베딩 로더·
검색/RRF 로직을 따로 구현해 쓰고 있어(환경 독립 아키텍처·PostgreSQL 저장 등 요구사항이
달랐음), 이 파일의 Embedder/VectorStore/HybridSearcher/ingest()와 한↔영 동의어 보강
로직은 한 번도 호출되지 않는 죽은 코드였다.
"""

import re


# ── 청킹 상수 (PRD 4차 수정 기준: 512토큰 / 50토큰 오버랩) ──────────────────────
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

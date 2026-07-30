# parser.py — PDF 파이프라인 입구
#
# [2026-07-28 교체] pdfplumber+camelot-py+EasyOCR+img2table로 직접 구현했던
# 텍스트/표/OCR 추출을 core/docling_parser.py(Docling 통합 파서)로 전부 이관했다.
# 이 파일은 이제 두 가지만 담당한다:
#   1. count_pdf_pages() — 본격 파싱 전에 페이지 수만 가볍게 세는 사전 체크
#      (업로드 즉시 응답을 위해 무거운 파싱 없이 페이지 상한을 확인해야 함)
#   2. parse_pdf() — Docling으로 위임하는 입구 함수
import os

import pypdfium2 as pdfium

from core.docling_parser import parse_with_docling

# 안전장치 — 대용량 문서가 파싱 파이프라인(청킹·임베딩 등)에 무제한으로 부담을
# 주지 않도록 문서 단위 페이지 수 상한을 둔다(STEP 2-A/3-B 결론, 이관 후에도 유지).
# SOFT_PAGE_LIMIT 초과: 처리는 그대로 진행하되 로그만 남긴다.
# HARD_PAGE_LIMIT 초과: 처리를 아예 시작하지 않고 즉시 실패 처리한다.
SOFT_PAGE_LIMIT = int(os.environ.get("SOFT_PAGE_LIMIT", "500"))
HARD_PAGE_LIMIT = int(os.environ.get("HARD_PAGE_LIMIT", "3000"))


def count_pdf_pages(path_or_fp) -> int:
    """PDF의 페이지 수만 센다 (텍스트 추출 없음 — Docling 전체 변환보다 훨씬 가벼운 사전 체크).
    path_or_fp: 파일 경로(str) 또는 파일류 객체(BytesIO 등) — pypdfium2가 둘 다 받는다.
    main.py 업로드 핸들러(디스크 저장 전 메모리 바이트)와 parse_pdf(디스크 경로) 양쪽에서 재사용한다."""
    doc = pdfium.PdfDocument(path_or_fp)
    try:
        return len(doc)
    finally:
        doc.close()


def parse_pdf(filepath: str, source_file: str = None) -> dict:
    """PDF 파싱 입구. Docling에 그대로 위임한다(텍스트/표/OCR/저신뢰 플래그 전부 포함)."""
    return parse_with_docling(filepath, source_file or filepath)

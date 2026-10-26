# core/office_adapter.py — 비-PDF 파싱 입구
#
# [2026-07-28 교체] DOCX/PPTX/XLSX/HWP/HWPX를 LibreOffice headless 경유로 처리하던
# hwp_postprocess/ 패키지를 제거하고 Docling으로 통합했다.
# [2026-10-24] HWP/HWPX 지원 재도입 — Docling이 이 두 형식을 지원하지 않아서,
# 한컴 공개 명세를 보고 직접 구현한 core/hwp_parser.py로 보낸다(외부 프로그램 없음).
#
# 형식별 흐름:
#   PDF                     → core/parser.py parse_pdf 가 이 형식을 직접 반환
#   DOCX/PPTX/XLSX          → core/docling_parser.py parse_with_docling
#   HWP / HWPX              → core/hwp_parser.py parse_hwp / parse_hwpx
#   TXT/MD                  → 아래 간단 리더가 계약 딕셔너리를 직접 생성

from core.docling_parser import parse_with_docling
from core.hwp_parser import parse_hwp, parse_hwpx

# 텍스트 계열: 별도 파서 없이 파일 내용을 그대로 1페이지로 읽는다
TEXT_EXTENSIONS = {".txt", ".md"}


def parse_non_pdf(filepath: str, source_file: str, ext: str) -> dict:
    """PDF가 아닌 파일의 파싱 입구. 확장자에 따라 Docling, HWP 파서, 텍스트 리더로 보낸다."""
    ext = ext.lower()

    if ext in TEXT_EXTENSIONS:
        return _parse_text_file(filepath, source_file)
    if ext == ".hwp":
        return parse_hwp(filepath, source_file)
    if ext == ".hwpx":
        return parse_hwpx(filepath, source_file)

    return parse_with_docling(filepath, source_file)


def _parse_text_file(filepath: str, source_file: str) -> dict:
    """TXT/MD 간단 리더 — 파일 내용을 그대로 1페이지 텍스트로 반환."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return {
            "source_file": source_file,
            "status": "success",
            "error": None,
            "pages": [{
                "page": 1,
                "text": text,
                "method": "text",
                "ocr_confidence": None,
                "flagged": False,
            }],
        }
    except Exception as e:
        return {
            "source_file": source_file,
            "status": "failed",
            "error": f"파일 읽기 오류: {e}",
            "pages": [],
        }

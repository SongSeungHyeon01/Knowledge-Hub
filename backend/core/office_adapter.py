# core/office_adapter.py — 비-PDF 파싱 입구 + 출력 계약 변환 어댑터
#
# 백엔드의 모든 파싱 결과는 "파서 출력 계약" 딕셔너리 하나로 통일된다:
#   {
#     "source_file": str,
#     "status": "success" | "failed",
#     "error": str | None,
#     "pages": [ {"page", "text", "method", "ocr_confidence", "flagged"} ]
#   }
#
# 형식별 흐름:
#   PDF                        → core/parser.py parse_pdf 가 이 형식을 직접 반환
#   DOCX/PPTX/XLSX/HWP/HWPX    → ② hwp_postprocess.parse() (ParseResult 객체 반환)
#                                → 이 파일의 어댑터가 계약 딕셔너리로 변환
#   TXT/MD                     → 아래 간단 리더가 계약 딕셔너리를 직접 생성

import hwp_postprocess
from hwp_postprocess.models import ParseResult, ParseStatus

# 텍스트 계열: 별도 파서 없이 파일 내용을 그대로 1페이지로 읽는다
TEXT_EXTENSIONS = {".txt", ".md"}


def parse_non_pdf(filepath: str, source_file: str, ext: str) -> dict:
    """PDF가 아닌 파일의 파싱 입구. 확장자에 따라 ② 파서 또는 텍스트 리더로 보낸다."""
    ext = ext.lower()

    if ext in TEXT_EXTENSIONS:
        return _parse_text_file(filepath, source_file)

    # ② 송승현 hwp_postprocess 정본 호출 (docx/pptx/ppt/xlsx/xls/hwp/hwpx)
    result = hwp_postprocess.parse(filepath)
    return _to_contract(result, source_file)


def _to_contract(result: ParseResult, source_file: str) -> dict:
    """②의 ParseResult 객체 → 파서 출력 계약 딕셔너리 변환.

    상태 매핑:
      OK      → "success"
      FLAGGED → "success" (저신뢰는 파일이 아니라 페이지 속성 flagged로 표현)
      FAILED  → "failed"
    페이지 매핑:
      page_num → page, markdown(비면 raw_text) → text
      method/ocr_confidence/flagged 는 PageResult에 없는 필드라 기본값을 쓰되,
      pdf_parser shim이 여분 속성으로 실어 보낸 경우 그 값을 그대로 복원한다.
    """
    if result.status == ParseStatus.FAILED:
        return {
            "source_file": source_file,
            "status": "failed",
            "error": result.error or "파싱 실패 (원인 미상)",
            "pages": [],
        }

    pages = []
    for p in result.pages:
        text = p.markdown if p.markdown else p.raw_text
        pages.append({
            "page": p.page_num,
            "text": text,
            "method": getattr(p, "method", "text"),
            "ocr_confidence": getattr(p, "ocr_confidence", None),
            "flagged": bool(getattr(p, "flagged", False)),
        })

    return {
        "source_file": source_file,
        "status": "success",
        "error": None,
        "pages": pages,
    }


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

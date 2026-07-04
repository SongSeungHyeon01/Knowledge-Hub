"""PDF 파이프라인 연결 shim — pptx_parser가 기대하는 hwp_postprocess.pdf_parser 모듈.

[통합 추가 2026-07-04]
pptx_parser는 "PPTX → LibreOffice → PDF" 변환 후 이 모듈의 parse_pdf()를 불러
ParseResult '객체'를 기대한다 (result.source_file = ... 처럼 속성에 대입).
실제 PDF 파싱은 정본인 core/parser.py가 하고,
여기서는 그 딕셔너리 결과를 ParseResult 객체로 감싸기만 한다. (로직 없음)
"""
from core.parser import parse_pdf as _core_parse_pdf
from hwp_postprocess.models import ParseResult, PageResult, ParseStatus


def parse_pdf(pdf_path: str) -> ParseResult:
    result = _core_parse_pdf(pdf_path)

    if result["status"] == "failed":
        return ParseResult(
            source_file=result["source_file"],
            status=ParseStatus.FAILED,
            error=result["error"],
        )

    pages: list[PageResult] = []
    any_flagged = False
    for p in result["pages"]:
        pr = PageResult(page_num=p["page"], markdown=p["text"], raw_text=p["text"])
        # PDF 계약의 페이지 정보(method/ocr_confidence/flagged)는 PageResult에 없는
        # 필드라, 여분 속성으로 실어 보낸다. core/office_adapter.py가 계약 딕셔너리로
        # 되돌릴 때 이 속성들을 그대로 복원한다. (정보 소실 방지)
        pr.method = p["method"]
        pr.ocr_confidence = p["ocr_confidence"]
        pr.flagged = p["flagged"]
        any_flagged = any_flagged or bool(p["flagged"])
        pages.append(pr)

    return ParseResult(
        source_file=result["source_file"],
        status=ParseStatus.FLAGGED if any_flagged else ParseStatus.OK,
        error=None,
        pages=pages,
    )

"""PPTX 파서 — LibreOffice headless → PDF → 윤준서 PDF 파이프라인 연결"""
import os
import shutil

from app.services.parsing.models import ParseResult, ParseStatus
from app.services.pipeline.office._libreoffice import convert


def parse(file_path: str) -> ParseResult:
    """
    PPTX → LibreOffice headless → PDF 변환 후 윤준서의 PDF 파이프라인으로 처리.
    Linux 환경에서 EMF(Windows 전용 벡터 이미지)를 LibreOffice가 자체 렌더링해
    pdfplumber + camelot + EasyOCR 파이프라인으로 처리할 수 있게 한다.
    """
    source = str(file_path)
    pdf_path: str | None = None

    try:
        pdf_path = convert(source, "pdf")
        result = _call_pdf_pipeline(pdf_path)
        # source_file 은 원본 PPTX 경로로 덮어쓴다
        result.source_file = source
        return result
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))
    finally:
        # LibreOffice 가 생성한 임시 디렉터리 정리
        if pdf_path:
            tmp_dir = os.path.dirname(pdf_path)
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _call_pdf_pipeline(pdf_path: str) -> ParseResult:
    """
    윤준서 담당 PDF 파이프라인을 호출한다.
    윤준서가 pipeline/pdf/pdf_parser.py 를 구현하면 아래 import 가 동작한다.
    """
    try:
        from app.services.pipeline.pdf.pdf_parser import parse_pdf
        return parse_pdf(pdf_path)
    except ImportError:
        raise RuntimeError(
            "PDF 파이프라인 미구현: "
            "app/services/pipeline/pdf/pdf_parser.py 의 parse_pdf() 를 구현하세요 (담당: 윤준서)."
        )

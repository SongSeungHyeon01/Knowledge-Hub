"""HWP 파서 — LibreOffice headless → DOCX → python-docx"""
import os
import shutil

from app.services.parsing.models import ParseResult, ParseStatus
from app.services.pipeline.office._libreoffice import convert
from app.services.pipeline.office import docx_parser


def parse(file_path: str) -> ParseResult:
    """
    HWP → LibreOffice headless → DOCX → python-docx 파이프라인.
    pyhwp 는 AGPL-3.0(네트워크 서비스 소스 공개 의무)으로 미채택, LibreOffice 단일 경로 사용.
    변환 실패 시 status=FAILED + error 메시지 반환 (예외를 상위로 전파하지 않음).
    """
    source = str(file_path)
    docx_path: str | None = None

    try:
        docx_path = convert(source, "docx")
        result = docx_parser.parse(docx_path)
        result.source_file = source  # 원본 HWP 경로로 복원
        return result
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))
    finally:
        if docx_path:
            tmp_dir = os.path.dirname(docx_path)
            shutil.rmtree(tmp_dir, ignore_errors=True)

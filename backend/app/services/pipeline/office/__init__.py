"""Office 파일 파서 디스패처 (송승현 담당: DOCX, PPTX, XLSX, HWP, HWPX)"""
from pathlib import Path

from app.services.parsing.models import ParseResult, ParseStatus

_EXT_TO_MODULE = {
    ".docx": "app.services.pipeline.office.docx_parser",
    ".pptx": "app.services.pipeline.office.pptx_parser",
    ".xlsx": "app.services.pipeline.office.xlsx_parser",
    ".xls":  "app.services.pipeline.office.xlsx_parser",
    ".hwp":  "app.services.pipeline.office.hwp_parser",
    ".hwpx": "app.services.pipeline.office.hwpx_parser",
}


def parse(file_path: str) -> ParseResult:
    """파일 확장자를 보고 적합한 파서로 라우팅한다."""
    ext = Path(file_path).suffix.lower()
    module_name = _EXT_TO_MODULE.get(ext)

    if module_name is None:
        return ParseResult(
            source_file=str(file_path),
            status=ParseStatus.FAILED,
            error=f"지원하지 않는 파일 형식: {ext}",
        )

    import importlib
    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        return ParseResult(
            source_file=str(file_path),
            status=ParseStatus.FAILED,
            error=f"파서 모듈 임포트 실패 ({module_name}): {e}",
        )

    return mod.parse(file_path)


def supported_extensions() -> list[str]:
    return list(_EXT_TO_MODULE.keys())

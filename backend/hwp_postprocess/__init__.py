"""Office íŒŒì¼ íŒŒì„œ ë””ìŠ¤íŒ¨ì²˜ (ì†¡ìŠ¹í˜„ ë‹´ë‹¹: DOCX, PPTX, XLSX, HWP, HWPX)"""
from pathlib import Path

from hwp_postprocess.models import ParseResult, ParseStatus

_EXT_TO_MODULE = {
    ".docx": "app.services.pipeline.office.docx_parser",
    ".pptx": "app.services.pipeline.office.pptx_parser",
    ".xlsx": "app.services.pipeline.office.xlsx_parser",
    ".xls":  "app.services.pipeline.office.xlsx_parser",
    ".hwp":  "app.services.pipeline.office.hwp_parser",
    ".hwpx": "app.services.pipeline.office.hwpx_parser",
}


def parse(file_path: str) -> ParseResult:
    """íŒŒì¼ í™•ìž¥ìžë¥¼ ë³´ê³  ì í•©í•œ íŒŒì„œë¡œ ë¼ìš°íŒ…í•œë‹¤."""
    ext = Path(file_path).suffix.lower()
    module_name = _EXT_TO_MODULE.get(ext)

    if module_name is None:
        return ParseResult(
            source_file=str(file_path),
            status=ParseStatus.FAILED,
            error=f"ì§€ì›í•˜ì§€ ì•ŠëŠ” íŒŒì¼ í˜•ì‹: {ext}",
        )

    import importlib
    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        return ParseResult(
            source_file=str(file_path),
            status=ParseStatus.FAILED,
            error=f"íŒŒì„œ ëª¨ë“ˆ ìž„í¬íŠ¸ ì‹¤íŒ¨ ({module_name}): {e}",
        )

    return mod.parse(file_path)


def supported_extensions() -> list[str]:
    return list(_EXT_TO_MODULE.keys())


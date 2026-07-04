"""Office 파일 파서 디스패처 (송승현 담당: DOCX, PPTX, XLSX, HWP, HWPX)"""
from pathlib import Path

from hwp_postprocess.models import ParseResult, ParseStatus

# [통합 수정 2026-07-04] 옛 구조(app/services/pipeline/office/) 시절 모듈 경로가
# 문자열로 남아 있어 모든 확장자가 "임포트 실패"로 죽던 버그 → 현재 패키지 경로로 수정.
_EXT_TO_MODULE = {
    ".docx": "hwp_postprocess.docx_parser",
    ".pptx": "hwp_postprocess.pptx_parser",
    ".xlsx": "hwp_postprocess.xlsx_parser",
    ".xls":  "hwp_postprocess.xlsx_parser",
    ".hwp":  "hwp_postprocess.hwp_parser",
    ".hwpx": "hwp_postprocess.hwpx_parser",
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


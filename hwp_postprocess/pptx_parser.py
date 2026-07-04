"""PPTX íŒŒì„œ â€” LibreOffice headless â†’ PDF â†’ ìœ¤ì¤€ì„œ PDF íŒŒì´í”„ë¼ì¸ ì—°ê²°"""
import os
import shutil

from hwp_postprocess.models import ParseResult, ParseStatus
from hwp_postprocess._libreoffice import convert


def parse(file_path: str) -> ParseResult:
    """
    PPTX â†’ LibreOffice headless â†’ PDF ë³€í™˜ í›„ ìœ¤ì¤€ì„œì˜ PDF íŒŒì´í”„ë¼ì¸ìœ¼ë¡œ ì²˜ë¦¬.
    Linux í™˜ê²½ì—ì„œ EMF(Windows ì „ìš© ë²¡í„° ì´ë¯¸ì§€)ë¥¼ LibreOfficeê°€ ìžì²´ ë Œë”ë§í•´
    pdfplumber + camelot + EasyOCR íŒŒì´í”„ë¼ì¸ìœ¼ë¡œ ì²˜ë¦¬í•  ìˆ˜ ìžˆê²Œ í•œë‹¤.
    """
    source = str(file_path)
    pdf_path: str | None = None

    try:
        pdf_path = convert(source, "pdf")
        result = _call_pdf_pipeline(pdf_path)
        # source_file ì€ ì›ë³¸ PPTX ê²½ë¡œë¡œ ë®ì–´ì“´ë‹¤
        result.source_file = source
        return result
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))
    finally:
        # LibreOffice ê°€ ìƒì„±í•œ ìž„ì‹œ ë””ë ‰í„°ë¦¬ ì •ë¦¬
        if pdf_path:
            tmp_dir = os.path.dirname(pdf_path)
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _call_pdf_pipeline(pdf_path: str) -> ParseResult:
    """
    ìœ¤ì¤€ì„œ ë‹´ë‹¹ PDF íŒŒì´í”„ë¼ì¸ì„ í˜¸ì¶œí•œë‹¤.
    ìœ¤ì¤€ì„œê°€ pipeline/pdf/pdf_parser.py ë¥¼ êµ¬í˜„í•˜ë©´ ì•„ëž˜ import ê°€ ë™ìž‘í•œë‹¤.
    """
    try:
        from hwp_postprocess.pdf_parser import parse_pdf
        return parse_pdf(pdf_path)
    except ImportError:
        raise RuntimeError(
            "PDF íŒŒì´í”„ë¼ì¸ ë¯¸êµ¬í˜„: "
            "app/services/pipeline/pdf/pdf_parser.py ì˜ parse_pdf() ë¥¼ êµ¬í˜„í•˜ì„¸ìš” (ë‹´ë‹¹: ìœ¤ì¤€ì„œ)."
        )



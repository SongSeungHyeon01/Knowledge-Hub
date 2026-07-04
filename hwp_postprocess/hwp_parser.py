"""HWP íŒŒì„œ â€” LibreOffice headless â†’ DOCX â†’ python-docx"""
import os
import shutil

from hwp_postprocess.models import ParseResult, ParseStatus
from hwp_postprocess._libreoffice import convert
from hwp_postprocess import docx_parser


def parse(file_path: str) -> ParseResult:
    """
    HWP â†’ LibreOffice headless â†’ DOCX â†’ python-docx íŒŒì´í”„ë¼ì¸.
    pyhwp ëŠ” AGPL-3.0(ë„¤íŠ¸ì›Œí¬ ì„œë¹„ìŠ¤ ì†ŒìŠ¤ ê³µê°œ ì˜ë¬´)ìœ¼ë¡œ ë¯¸ì±„íƒ, LibreOffice ë‹¨ì¼ ê²½ë¡œ ì‚¬ìš©.
    ë³€í™˜ ì‹¤íŒ¨ ì‹œ status=FAILED + error ë©”ì‹œì§€ ë°˜í™˜ (ì˜ˆì™¸ë¥¼ ìƒìœ„ë¡œ ì „íŒŒí•˜ì§€ ì•ŠìŒ).
    """
    source = str(file_path)
    docx_path: str | None = None

    try:
        docx_path = convert(source, "docx")
        result = docx_parser.parse(docx_path)
        result.source_file = source  # ì›ë³¸ HWP ê²½ë¡œë¡œ ë³µì›
        return result
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))
    finally:
        if docx_path:
            tmp_dir = os.path.dirname(docx_path)
            shutil.rmtree(tmp_dir, ignore_errors=True)



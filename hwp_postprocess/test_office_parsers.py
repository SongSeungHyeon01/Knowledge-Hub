"""
ì†¡ìŠ¹í˜„ íŒŒíŠ¸ ë…ë¦½ í…ŒìŠ¤íŠ¸ ìŠ¤í¬ë¦½íŠ¸
ì‹¤í–‰: python test_office_parsers.py  (Team-3/backend í´ë”ì—ì„œ)

DOCX / XLSX / HWPX â€” ë”ë¯¸ íŒŒì¼ ìžë™ ìƒì„± í›„ íŒŒì„œ ê²€ì¦
HWP  / PPTX        â€” LibreOffice í•„ìš” (ë¯¸ì„¤ì¹˜ ì‹œ SKIP í‘œì‹œ)
"""
import sys
import os
import zipfile
import tempfile
import shutil
import traceback

# â”€â”€ ê²½ë¡œ ì„¤ì •: backend ë””ë ‰í„°ë¦¬ë¥¼ ìµœìš°ì„ ìœ¼ë¡œ ì¶”ê°€
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# â”€â”€ í„°ë¯¸ë„ ìƒ‰ìƒ
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ë”ë¯¸ íŒŒì¼ ìƒì„± í—¬í¼
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def make_docx(path: str):
    from docx import Document
    doc = Document()
    doc.add_heading("KM í”Œëž«í¼ ì„¤ê³„ì„œ", level=1)
    doc.add_heading("1. ê°œìš”", level=2)
    doc.add_paragraph("ì´ ë¬¸ì„œëŠ” ì‚¬ë‚´ ì§€ì‹ê´€ë¦¬ í”Œëž«í¼ì˜ ì„¤ê³„ë¥¼ ì„¤ëª…í•©ë‹ˆë‹¤.")
    doc.add_paragraph("English paragraph for multilingual support.")

    tbl = doc.add_table(rows=3, cols=3)
    headers = ["êµ¬ì„±ìš”ì†Œ", "ë‹´ë‹¹", "ê¸°ìˆ ìŠ¤íƒ"]
    data = [
        ["íŒŒì‹±", "ì†¡ìŠ¹í˜„", "python-docx / openpyxl"],
        ["ê²€ìƒ‰", "ê¹€íƒœí›ˆ", "turbovec / BM25"],
    ]
    for i, h in enumerate(headers):
        tbl.cell(0, i).text = h
    for r, row in enumerate(data, start=1):
        for c, val in enumerate(row):
            tbl.cell(r, c).text = val

    doc.save(path)


def make_xlsx(path: str):
    from openpyxl import Workbook
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "ì§ì›ëª©ë¡"
    ws1.append(["ì´ë¦„", "ë¶€ì„œ", "ìž…ì‚¬ì—°ë„", "ì—­í• "])
    ws1.append(["ìœ¤ì¤€ì„œ", "ê°œë°œíŒ€", 2023, "PDF íŒŒì‹±"])
    ws1.append(["ì†¡ìŠ¹í˜„", "ê°œë°œíŒ€", 2023, "ì˜¤í”¼ìŠ¤ íŒŒì‹±"])
    ws1.append(["ê¹€íƒœí›ˆ", "ê°œë°œíŒ€", 2023, "ìž„ë² ë”©/ê²€ìƒ‰"])
    ws1.append(["ê¹€ê¸°ë¹ˆ", "ê°œë°œíŒ€", 2023, "ë°±ì—”ë“œ/UI"])

    ws2 = wb.create_sheet("ê¸°ìˆ ìŠ¤íƒ")
    ws2.append(["ë¼ì´ë¸ŒëŸ¬ë¦¬", "ë¼ì´ì„ ìŠ¤", "ìš©ë„"])
    ws2.append(["pdfplumber", "MIT", "PDF í…ìŠ¤íŠ¸ ì¶”ì¶œ"])
    ws2.append(["camelot-py", "MIT", "PDF í‘œ ì¶”ì¶œ"])
    ws2.append(["python-docx", "MIT", "DOCX íŒŒì‹±"])
    ws2.append(["openpyxl", "MIT", "XLSX íŒŒì‹±"])

    wb.save(path)


def make_hwpx(path: str):
    section_xml = """<?xml version="1.0" encoding="UTF-8"?>
<hml:sec xmlns:hml="http://www.hancom.co.kr/hwpml/2012/section">
  <hml:p id="0">
    <hml:run id="0">
      <hml:t>HWPX í…ŒìŠ¤íŠ¸ ë¬¸ì„œìž…ë‹ˆë‹¤.</hml:t>
    </hml:run>
  </hml:p>
  <hml:p id="1">
    <hml:run id="1">
      <hml:t>This is an English paragraph in HWPX format.</hml:t>
    </hml:run>
  </hml:p>
  <hml:tbl>
    <hml:tr>
      <hml:tc>
        <hml:p><hml:run><hml:t>í•­ëª©</hml:t></hml:run></hml:p>
      </hml:tc>
      <hml:tc>
        <hml:p><hml:run><hml:t>ë‚´ìš©</hml:t></hml:run></hml:p>
      </hml:tc>
    </hml:tr>
    <hml:tr>
      <hml:tc>
        <hml:p><hml:run><hml:t>íŒŒì¼í˜•ì‹</hml:t></hml:run></hml:p>
      </hml:tc>
      <hml:tc>
        <hml:p><hml:run><hml:t>HWPX (ZIP+XML)</hml:t></hml:run></hml:p>
      </hml:tc>
    </hml:tr>
    <hml:tr>
      <hml:tc>
        <hml:p><hml:run><hml:t>íŒŒì„œ</hml:t></hml:run></hml:p>
      </hml:tc>
      <hml:tc>
        <hml:p><hml:run><hml:t>zipfile + lxml</hml:t></hml:run></hml:p>
      </hml:tc>
    </hml:tr>
  </hml:tbl>
  <hml:p id="2">
    <hml:run id="2">
      <hml:t>í‘œ ì•„ëž˜ ë‹¨ë½ â€” í…ŒìŠ¤íŠ¸ ë³´ì¡´ í™•ì¸ìš©.</hml:t>
    </hml:run>
  </hml:p>
</hml:sec>""".encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Contents/section0.xml", section_xml)
        zf.writestr("META-INF/container.xml",
                    b'<?xml version="1.0"?><container/>')


def make_hwp_dummy(path: str):
    """ì‹¤ì œ HWP ë°”ì´ë„ˆë¦¬ëŠ” ë§Œë“¤ ìˆ˜ ì—†ìœ¼ë¯€ë¡œ ë¹ˆ íŒŒì¼ë¡œ ëŒ€ì²´ (ë³€í™˜ ì‹¤íŒ¨ ì˜ˆìƒ)."""
    with open(path, "wb") as f:
        f.write(b"HWP Document File V3.0\x1a\x01\x02\x03")


def make_pptx(path: str):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "KM í”Œëž«í¼ ë°œí‘œ"
    slide.placeholders[1].text = "ì‚¬ë‚´ ì§€ì‹ê´€ë¦¬ í”Œëž«í¼\níŒ€ 3ì¡°"
    prs.save(path)


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ê²€ì¦ í—¬í¼
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    detail_str = f"  â†’ {detail}" if detail else ""
    print(f"    {'âœ“' if condition else 'âœ—'} {label}{detail_str}")
    return condition


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'â”€'*50}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'â”€'*50}{RESET}")


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ê°œë³„ í…ŒìŠ¤íŠ¸ í•¨ìˆ˜
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_models():
    section("models.py â€” ParseResult / PageResult / ParseStatus")
    from hwp_postprocess.models import ParseResult, PageResult, ParseStatus
    ok = True

    r = ParseResult(source_file="test.docx", status=ParseStatus.OK,
                    pages=[PageResult(page_num=1, markdown="# ì œëª©")])
    ok &= check("ParseResult ìƒì„±", r.source_file == "test.docx")
    ok &= check("PageResult í¬í•¨",  len(r.pages) == 1)
    ok &= check("markdown ê°’",      r.pages[0].markdown == "# ì œëª©")
    ok &= check("status=OK",        r.status == ParseStatus.OK)

    r2 = ParseResult(source_file="bad.docx", status=ParseStatus.FAILED, error="ì—ëŸ¬ë©”ì‹œì§€")
    ok &= check("status=FAILED + error", r2.error == "ì—ëŸ¬ë©”ì‹œì§€")

    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


def test_docx(tmp: str):
    section("DOCX íŒŒì„œ")
    try:
        from hwp_postprocess import docx_parser
        from hwp_postprocess.models import ParseStatus
    except ImportError as e:
        print(f"  {SKIP} python-docx ë¯¸ì„¤ì¹˜ ({e})")
        return None

    path = os.path.join(tmp, "test.docx")
    make_docx(path)
    result = docx_parser.parse(path)

    ok = True
    ok &= check("status=OK",            result.status == ParseStatus.OK,
                result.error or "")
    ok &= check("pages 1ê°œ",            len(result.pages) == 1)

    if result.pages:
        md = result.pages[0].markdown
        ok &= check("í—¤ë”© í¬í•¨ (# ê¸°í˜¸)",  "#" in md, md[:80])
        ok &= check("ë³¸ë¬¸ í…ìŠ¤íŠ¸ í¬í•¨",     "ì„¤ê³„" in md)
        ok &= check("í‘œ í—¤ë” í¬í•¨",        "êµ¬ì„±ìš”ì†Œ" in md)
        ok &= check("í‘œ ë°ì´í„° í¬í•¨",       "ì†¡ìŠ¹í˜„" in md)
        ok &= check("Markdown í‘œ í˜•ì‹",   "| --- |" in md or "|---|" in md)
        print(f"\n  {CYAN}--- Markdown ì¶œë ¥ ë¯¸ë¦¬ë³´ê¸° ---{RESET}")
        for line in md.split("\n")[:15]:
            print(f"  {line}")
        if md.count("\n") >= 15:
            print("  ...")

    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


def test_xlsx(tmp: str):
    section("XLSX íŒŒì„œ")
    try:
        from hwp_postprocess import xlsx_parser
        from hwp_postprocess.models import ParseStatus
    except ImportError as e:
        print(f"  {SKIP} openpyxl ë¯¸ì„¤ì¹˜ ({e})")
        return None

    path = os.path.join(tmp, "test.xlsx")
    make_xlsx(path)
    result = xlsx_parser.parse(path)

    ok = True
    ok &= check("status=OK",            result.status == ParseStatus.OK,
                result.error or "")
    ok &= check("ì‹œíŠ¸ 2ê°œ â†’ pages 2ê°œ", len(result.pages) == 2)

    if len(result.pages) >= 1:
        md1 = result.pages[0].markdown
        ok &= check("ì‹œíŠ¸ëª… í¬í•¨ (## ì§ì›ëª©ë¡)",  "ì§ì›ëª©ë¡" in md1)
        ok &= check("ë°ì´í„° í–‰ í¬í•¨",            "ì†¡ìŠ¹í˜„" in md1)
        ok &= check("Markdown í‘œ í˜•ì‹",          "|" in md1)

    if len(result.pages) >= 2:
        md2 = result.pages[1].markdown
        ok &= check("2ë²ˆ ì‹œíŠ¸ (## ê¸°ìˆ ìŠ¤íƒ)",    "ê¸°ìˆ ìŠ¤íƒ" in md2)
        ok &= check("2ë²ˆ ì‹œíŠ¸ ë°ì´í„°",           "pdfplumber" in md2)

    if result.pages:
        print(f"\n  {CYAN}--- ì‹œíŠ¸1 ë¯¸ë¦¬ë³´ê¸° ---{RESET}")
        for line in result.pages[0].markdown.split("\n")[:10]:
            print(f"  {line}")

    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


def test_hwpx(tmp: str):
    section("HWPX íŒŒì„œ")
    try:
        from hwp_postprocess import hwpx_parser
        from hwp_postprocess.models import ParseStatus
    except ImportError as e:
        print(f"  {SKIP} lxml ë¯¸ì„¤ì¹˜ ({e})")
        return None

    path = os.path.join(tmp, "test.hwpx")
    make_hwpx(path)
    result = hwpx_parser.parse(path)

    ok = True
    ok &= check("status=OK",             result.status == ParseStatus.OK,
                result.error or "")
    ok &= check("pages 1ê°œ (section 1ê°œ)", len(result.pages) == 1)

    if result.pages:
        md = result.pages[0].markdown
        ok &= check("ë³¸ë¬¸ í…ìŠ¤íŠ¸ ì¶”ì¶œ",     "HWPX" in md)
        ok &= check("ì˜ë¬¸ í…ìŠ¤íŠ¸ ì¶”ì¶œ",     "English" in md)
        ok &= check("í‘œ í—¤ë” ì¶”ì¶œ",         "í•­ëª©" in md)
        ok &= check("í‘œ ë°ì´í„° ì¶”ì¶œ",       "zipfile" in md)
        ok &= check("í‘œ ì•„ëž˜ ë‹¨ë½ ë³´ì¡´",    "í‘œ ì•„ëž˜" in md)
        ok &= check("Markdown í‘œ í˜•ì‹",    "|" in md)

        print(f"\n  {CYAN}--- Markdown ì¶œë ¥ ë¯¸ë¦¬ë³´ê¸° ---{RESET}")
        for line in md.split("\n"):
            print(f"  {line}")

    # ì—£ì§€ì¼€ì´ìŠ¤: ìž˜ëª»ëœ ZIP
    section_bad = os.path.join(tmp, "bad.hwpx")
    with open(section_bad, "wb") as f:
        f.write(b"not a zip file")
    r_bad = hwpx_parser.parse(section_bad)
    ok &= check("ì†ìƒ íŒŒì¼ â†’ FAILED (í¬ëž˜ì‹œ ì—†ìŒ)",
                r_bad.status.value == "failed")

    # ì—£ì§€ì¼€ì´ìŠ¤: section*.xml ì—†ëŠ” ZIP
    empty_hwpx = os.path.join(tmp, "empty.hwpx")
    with zipfile.ZipFile(empty_hwpx, "w") as zf:
        zf.writestr("dummy.txt", "nothing")
    r_empty = hwpx_parser.parse(empty_hwpx)
    ok &= check("section ì—†ëŠ” ZIP â†’ FAILED", r_empty.status.value == "failed")

    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


def test_hwp_libreoffice(tmp: str):
    section("HWP íŒŒì„œ (LibreOffice í•„ìš”)")
    from hwp_postprocess._libreoffice import _find_soffice
    if not _find_soffice():
        print(f"  {SKIP} LibreOffice ë¯¸ì„¤ì¹˜ â€” Railway ë°°í¬ í™˜ê²½ì—ì„œë§Œ ë™ìž‘")
        return None

    from hwp_postprocess import hwp_parser
    from hwp_postprocess.models import ParseStatus

    path = os.path.join(tmp, "test.hwp")
    make_hwp_dummy(path)
    result = hwp_parser.parse(path)
    # ë”ë¯¸ HWPëŠ” ì‹¤ì œ HWPê°€ ì•„ë‹ˆë¯€ë¡œ FAILED ì˜ˆìƒ â€” í•µì‹¬ì€ í¬ëž˜ì‹œ ì—†ì´ ë°˜í™˜
    ok = check("LibreOffice ë³€í™˜ ì‹¤íŒ¨ ì‹œ FAILED ë°˜í™˜ (í¬ëž˜ì‹œ ì—†ìŒ)",
               result.status.value in ("ok", "failed"))
    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


def test_pptx_libreoffice(tmp: str):
    section("PPTX íŒŒì„œ (LibreOffice + ìœ¤ì¤€ì„œ PDF íŒŒì´í”„ë¼ì¸ í•„ìš”)")
    from hwp_postprocess._libreoffice import _find_soffice
    if not _find_soffice():
        print(f"  {SKIP} LibreOffice ë¯¸ì„¤ì¹˜ â€” Railway ë°°í¬ í™˜ê²½ì—ì„œë§Œ ë™ìž‘")
        return None

    try:
        make_pptx(os.path.join(tmp, "test.pptx"))
    except ImportError:
        print(f"  {SKIP} python-pptx ë¯¸ì„¤ì¹˜ (ë”ë¯¸ ìƒì„± ë¶ˆê°€)")
        return None

    from hwp_postprocess import pptx_parser
    result = pptx_parser.parse(os.path.join(tmp, "test.pptx"))
    # ìœ¤ì¤€ì„œ PDF íŒŒì´í”„ë¼ì¸ ë¯¸êµ¬í˜„ì´ë¯€ë¡œ FAILED ì˜ˆìƒ
    ok = check("LibreOffice ë³€í™˜ ì‹œë„ í›„ NotImplementedError â†’ FAILED ë°˜í™˜",
               result.status.value == "failed")
    ok &= check("error ë©”ì‹œì§€ í¬í•¨",  result.error is not None)
    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


def test_dispatcher(tmp: str):
    section("ë””ìŠ¤íŒ¨ì²˜ (__init__.py) â€” í™•ìž¥ìž ë¼ìš°íŒ…")
    from hwp_postprocess import parse, supported_extensions
    from hwp_postprocess.models import ParseStatus

    # ì§€ì›í•˜ì§€ ì•ŠëŠ” í™•ìž¥ìž
    result = parse(os.path.join(tmp, "test.pdf"))
    ok = check(".pdf â†’ FAILED (office ë‹´ë‹¹ ì•„ë‹˜)",
               result.status == ParseStatus.FAILED)

    result2 = parse(os.path.join(tmp, "test.mp4"))
    ok &= check(".mp4 â†’ FAILED", result2.status == ParseStatus.FAILED)

    exts = supported_extensions()
    ok &= check("ì§€ì› í™•ìž¥ìž ëª©ë¡ í™•ì¸",
                all(e in exts for e in [".docx", ".xlsx", ".hwp", ".hwpx", ".pptx"]),
                str(exts))

    print(f"\n  ê²°ê³¼: {PASS if ok else FAIL}")
    return ok


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ë©”ì¸
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    print(f"\n{BOLD}{'='*50}")
    print("  ì†¡ìŠ¹í˜„ íŒŒíŠ¸ ë…ë¦½ í…ŒìŠ¤íŠ¸")
    print(f"{'='*50}{RESET}")

    tmp = tempfile.mkdtemp(prefix="km_test_")
    print(f"  ìž„ì‹œ ë””ë ‰í„°ë¦¬: {tmp}\n")

    results = {}
    try:
        results["models"]     = test_models()
        results["DOCX"]       = test_docx(tmp)
        results["XLSX"]       = test_xlsx(tmp)
        results["HWPX"]       = test_hwpx(tmp)
        results["HWP"]        = test_hwp_libreoffice(tmp)
        results["PPTX"]       = test_pptx_libreoffice(tmp)
        results["dispatcher"] = test_dispatcher(tmp)
    except Exception:
        print(f"\n{RED}ì˜ˆìƒì¹˜ ëª»í•œ ì˜¤ë¥˜:{RESET}")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # â”€â”€ ìµœì¢… ìš”ì•½
    print(f"\n{BOLD}{'='*50}")
    print("  ìµœì¢… ê²°ê³¼ ìš”ì•½")
    print(f"{'='*50}{RESET}")
    passed = skipped = failed = 0
    for name, res in results.items():
        if res is True:
            print(f"  {PASS}  {name}")
            passed += 1
        elif res is False:
            print(f"  {FAIL}  {name}")
            failed += 1
        else:
            print(f"  {SKIP}  {name}")
            skipped += 1

    print(f"\n  í†µê³¼: {passed}  ì‹¤íŒ¨: {failed}  ê±´ë„ˆëœ€: {skipped}")
    if failed == 0:
        print(f"\n{GREEN}{BOLD}  ëª¨ë“  í…ŒìŠ¤íŠ¸ í†µê³¼ (ë˜ëŠ” SKIP){RESET}\n")
    else:
        print(f"\n{RED}{BOLD}  {failed}ê°œ í…ŒìŠ¤íŠ¸ ì‹¤íŒ¨ â€” ìœ„ ìƒì„¸ ë‚´ìš© í™•ì¸{RESET}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())




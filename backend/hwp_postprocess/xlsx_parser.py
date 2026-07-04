"""XLSX íŒŒì„œ â€” openpyxl â†’ ì‹œíŠ¸ë³„ Markdown í…Œì´ë¸”"""
from openpyxl import load_workbook

from hwp_postprocess.models import ParseResult, PageResult, ParseStatus


def parse(file_path: str) -> ParseResult:
    source = str(file_path)
    try:
        # data_only=True: ìˆ˜ì‹ ëŒ€ì‹  ìºì‹œëœ ê°’ì„ ì½ëŠ”ë‹¤
        wb = load_workbook(file_path, data_only=True)
        pages: list[PageResult] = []

        for idx, sheet_name in enumerate(wb.sheetnames, start=1):
            ws = wb[sheet_name]
            md = _sheet_to_markdown(ws, sheet_name)
            pages.append(PageResult(page_num=idx, markdown=md))

        if not pages:
            return ParseResult(source_file=source, status=ParseStatus.FAILED, error="ì‹œíŠ¸ ì—†ìŒ")

        return ParseResult(source_file=source, status=ParseStatus.OK, pages=pages)
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))


def _sheet_to_markdown(ws, sheet_name: str) -> str:
    rows: list[list[str]] = []

    for row in ws.iter_rows():
        cells = [str(cell.value) if cell.value is not None else "" for cell in row]
        # ì™„ì „ížˆ ë¹ˆ í–‰ ì œì™¸
        if any(c.strip() for c in cells):
            rows.append([c.replace("|", "\\|").replace("\n", " ") for c in cells])

    if not rows:
        return f"## {sheet_name}\n\n(ë¹ˆ ì‹œíŠ¸)"

    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    lines = [f"## {sheet_name}", ""]
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


"""HWPX íŒŒì„œ â€” zipfile + lxml, section*.xml ì§ì ‘ íŒŒì‹±"""
import io
import zipfile
from pathlib import Path

from lxml import etree

from hwp_postprocess.models import ParseResult, PageResult, ParseStatus


def parse(file_path: str) -> ParseResult:
    source = str(file_path)
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            section_names = sorted(
                n for n in zf.namelist()
                if n.startswith("Contents/section") and n.endswith(".xml")
            )
            if not section_names:
                return ParseResult(
                    source_file=source,
                    status=ParseStatus.FAILED,
                    error="section*.xml íŒŒì¼ì„ ì°¾ì„ ìˆ˜ ì—†ìŠµë‹ˆë‹¤.",
                )

            pages: list[PageResult] = []
            for idx, name in enumerate(section_names, start=1):
                xml_bytes = zf.read(name)
                md = _parse_section(xml_bytes)
                pages.append(PageResult(page_num=idx, markdown=md))

        return ParseResult(source_file=source, status=ParseStatus.OK, pages=pages)
    except zipfile.BadZipFile:
        return ParseResult(
            source_file=source,
            status=ParseStatus.FAILED,
            error="ìœ íš¨í•˜ì§€ ì•Šì€ HWPX íŒŒì¼ìž…ë‹ˆë‹¤.",
        )
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))


def _parse_section(xml_bytes: bytes) -> str:
    """section*.xml í•œ ê°œë¥¼ íŒŒì‹±í•´ Markdown ë¬¸ìžì—´ë¡œ ë°˜í™˜í•œë‹¤."""
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as e:
        return f"(XML íŒŒì‹± ì˜¤ë¥˜: {e})"

    blocks: list[str] = []

    for child in root:
        tag = _localname(child.tag)
        if tag == "p":
            text = _get_para_text(child).strip()
            if text:
                blocks.append(text)
        elif tag == "tbl":
            rows = _get_table_rows(child)
            if rows:
                blocks.append(_rows_to_markdown(rows))

    return "\n\n".join(b for b in blocks if b)


def _get_para_text(para_elem) -> str:
    """ë‹¨ë½ ìš”ì†Œì—ì„œ ëª¨ë“  <t> í…ìŠ¤íŠ¸ë¥¼ ì¶”ì¶œí•œë‹¤."""
    texts: list[str] = []
    for elem in para_elem.iter():
        if _localname(elem.tag) == "t" and elem.text:
            texts.append(elem.text)
    return " ".join(texts)


def _get_table_rows(tbl_elem) -> list[list[str]]:
    """í‘œ ìš”ì†Œì—ì„œ í–‰Â·ì…€ í…ìŠ¤íŠ¸ë¥¼ ì¶”ì¶œí•œë‹¤."""
    rows: list[list[str]] = []
    for tr in tbl_elem:
        if _localname(tr.tag) != "tr":
            continue
        cells: list[str] = []
        for tc in tr:
            if _localname(tc.tag) != "tc":
                continue
            cell_text = " ".join(
                elem.text
                for elem in tc.iter()
                if _localname(elem.tag) == "t" and elem.text
            ).strip()
            cells.append(cell_text.replace("|", "\\|"))
        if cells:
            rows.append(cells)
    return rows


def _rows_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _localname(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


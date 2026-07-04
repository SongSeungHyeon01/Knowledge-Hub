"""DOCX 파서 — python-docx + embedded 이미지 EasyOCR+img2table"""
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

from app.services.parsing.models import ParseResult, PageResult, ParseStatus
from app.services.pipeline.office._image_ocr import extract_image_markdown


def parse(file_path: str) -> ParseResult:
    source = str(file_path)
    try:
        doc = Document(file_path)
        markdown = _doc_to_markdown(doc)
        return ParseResult(
            source_file=source,
            status=ParseStatus.OK,
            pages=[PageResult(page_num=1, markdown=markdown)],
        )
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))


def _doc_to_markdown(doc: Document) -> str:
    blocks: list[str] = []

    for child in doc.element.body:
        tag = _localname(child.tag)

        if tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            style = para.style.name if para.style else ""

            if text:
                if "Heading" in style:
                    level = _heading_level(style)
                    blocks.append(f"{'#' * level} {text}")
                else:
                    blocks.append(text)

            # 단락 내 embedded 이미지
            for img_bytes in _get_para_images(child, doc.part):
                md = extract_image_markdown(img_bytes)
                if md:
                    blocks.append(md)

        elif tag == "tbl":
            tbl = Table(child, doc)
            md = _table_to_markdown(tbl)
            if md:
                blocks.append(md)

    return "\n\n".join(b for b in blocks if b.strip())


def _get_para_images(para_elem, part) -> list[bytes]:
    images: list[bytes] = []
    for blip in para_elem.iter(qn("a:blip")):
        embed = blip.get(qn("r:embed"))
        if embed and embed in part.rels:
            rel = part.rels[embed]
            if "image" in rel.reltype.lower():
                try:
                    images.append(rel.target_part.blob)
                except Exception:
                    pass
    return images


def _table_to_markdown(tbl: Table) -> str:
    rows: list[list[str]] = []
    for row in tbl.rows:
        seen: set[int] = set()
        cells: list[str] = []
        for cell in row.cells:
            cid = id(cell._tc)
            if cid not in seen:
                seen.add(cid)
                cells.append(
                    cell.text.strip().replace("\n", " ").replace("|", "\\|")
                )
        rows.append(cells)

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


def _heading_level(style_name: str) -> int:
    for i in range(1, 7):
        if str(i) in style_name:
            return i
    return 2

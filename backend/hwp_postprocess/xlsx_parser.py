"""XLSX 파서 — openpyxl(XLSX)/xlrd(구형식 XLS) → 시트별 Markdown 테이블"""
from pathlib import Path

from openpyxl import load_workbook

from hwp_postprocess.models import ParseResult, PageResult, ParseStatus


def parse(file_path: str) -> ParseResult:
    source = str(file_path)
    try:
        if Path(file_path).suffix.lower() == ".xls":
            # [수정 2026-07-25] .xls(옛 OLE2/BIFF 바이너리 포맷)는 openpyxl이 애초에
            # 읽을 수 없는 포맷이다("openpyxl does not support the old .xls file
            # format"). 처음엔 LibreOffice로 .xlsx 변환을 거치는 방식을 썼지만,
            # LibreOffice가 원본을 못 열 때 종료 코드는 0(성공)으로 반환하면서
            # 변환 결과 파일만 없는 조용한 실패가 발생해 진단이 어려웠다. xlrd는
            # BIFF 바이너리를 직접 읽어 LibreOffice 프로세스 호출 자체가 필요 없고,
            # 못 읽는 파일은 XLRDError로 바로 실패 사유가 드러난다(BSD 라이선스).
            pages = _parse_xls(file_path)
        else:
            # data_only=True: 수식 대신 캐시된 값을 읽는다
            wb = load_workbook(file_path, data_only=True)
            pages = [
                PageResult(page_num=idx, markdown=_sheet_to_markdown_openpyxl(wb[name], name))
                for idx, name in enumerate(wb.sheetnames, start=1)
            ]

        if not pages:
            return ParseResult(source_file=source, status=ParseStatus.FAILED, error="시트 없음")

        return ParseResult(source_file=source, status=ParseStatus.OK, pages=pages)
    except Exception as e:
        return ParseResult(source_file=source, status=ParseStatus.FAILED, error=str(e))


def _parse_xls(file_path: str) -> list[PageResult]:
    import xlrd
    wb = xlrd.open_workbook(file_path)
    return [
        PageResult(page_num=idx, markdown=_sheet_to_markdown_xlrd(wb.sheet_by_name(name), name))
        for idx, name in enumerate(wb.sheet_names(), start=1)
    ]


def _xlrd_cell_str(value) -> str:
    if value == "":
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _sheet_to_markdown_xlrd(ws, sheet_name: str) -> str:
    rows: list[list[str]] = []
    for r in range(ws.nrows):
        cells = [_xlrd_cell_str(ws.cell_value(r, c)) for c in range(ws.ncols)]
        if any(c.strip() for c in cells):
            rows.append([c.replace("|", "\\|").replace("\n", " ") for c in cells])
    return _rows_to_markdown(rows, sheet_name)


def _sheet_to_markdown_openpyxl(ws, sheet_name: str) -> str:
    rows: list[list[str]] = []
    for row in ws.iter_rows():
        cells = [str(cell.value) if cell.value is not None else "" for cell in row]
        # 완전히 빈 행 제외
        if any(c.strip() for c in cells):
            rows.append([c.replace("|", "\\|").replace("\n", " ") for c in cells])
    return _rows_to_markdown(rows, sheet_name)


def _rows_to_markdown(rows: list[list[str]], sheet_name: str) -> str:
    if not rows:
        return f"## {sheet_name}\n\n(빈 시트)"

    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    lines = [f"## {sheet_name}", ""]
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


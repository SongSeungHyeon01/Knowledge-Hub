"""hwp_parser.py — 한글(HWP 5.0 / HWPX) 문서 텍스트 추출

본 제품은 한글과컴퓨터의 한글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
(한컴 공개 명세 '저작권' 항목이 이 문구를 소스·매뉴얼·UI에 모두 적도록 요구한다.
 명세 원문은 제품명을 로고 글자로 적었고, 여기서는 일반 글꼴의 "한글"로 옮겼다.)

[2026-10-24 도입] Docling이 HWP·HWPX를 지원하지 않아(docling-project.github.io/docling/
usage/supported_formats/ 목록에 없음) 별도 경로로 처리한다. 예전 hwp_postprocess는
HWP를 LibreOffice + H2Orestart로 DOCX 변환한 뒤 읽었는데, 외부 프로그램과 GPL 계열
확장 없이 처리하려고 한컴 공개 명세(「한글 문서 파일 형식 5.0」 revision 1.3)를 보고
직접 구현했다. 파일 컨테이너는 olefile(BSD-2-Clause)로 열고, 본문 압축은 표준 zlib로 푼다.

명세에서 이 구현이 의존하는 부분:
  - FileHeader: 32바이트 signature "HWP Document File", 오프셋 36 DWORD 속성
    (bit0 압축, bit1 암호, bit2 배포용 문서)                      — 3.2.1, 표 3
  - BodyText/Section%d 스트림 = 레코드 나열                        — 3.2.3
  - 레코드 헤더 32bit = TagID(10) | Level(10) | Size(12),
    Size가 0xFFF이면 뒤따르는 DWORD가 실제 길이                    — 4.1
  - HWPTAG_BEGIN=0x010, PARA_TEXT=+51, CTRL_HEADER=+55, LIST_HEADER=+56, TABLE=+61 — 4.3
  - 문단 텍스트의 제어 문자(0~31): 문자/인라인/확장 컨트롤, 인라인·확장은 8 WCHAR — 표 6
  - 표 개체 속성: UINT32 속성 다음 UINT16 RowCount, UINT16 nCols    — 표 75
  - 셀 리스트 = 문단 리스트 헤더 + 셀 속성(열 주소, 행 주소, …)     — 표 79, 80

출력은 core/office_adapter.py와 같은 파서 계약을 따른다:
    {"source_file", "status", "error", "pages": [{"page", "text", "method",
     "ocr_confidence", "flagged"}]}

쪽 번호: HWP·HWPX는 DOCX와 마찬가지로 쪽이 파일에 고정돼 있지 않아 구역(Section) 하나를
한 묶음(page)으로 돌려준다. 줄 배치 정보로 쪽을 추정해 봤지만(2026-10-24, 37쪽짜리 문서를
한컴 PDF와 대조) "페이지의 첫 줄" 플래그는 저장돼 있지 않았고, 줄 세로 위치로 추정하면
여러 쪽에 걸친 표 때문에 26쪽으로 잡히고 최대 10쪽까지 어긋났다. 틀린 쪽 번호를 보여주지
않으려고 추정을 넣지 않았다.
"""

from __future__ import annotations

import struct
import zipfile
import zlib

import olefile
from lxml import etree

# ── HWP 5.0 레코드 태그 (명세 4.3, HWPTAG_BEGIN = 0x010) ───────────────────────
_HWPTAG_BEGIN = 0x010
TAG_PARA_TEXT = _HWPTAG_BEGIN + 51
TAG_CTRL_HEADER = _HWPTAG_BEGIN + 55
TAG_LIST_HEADER = _HWPTAG_BEGIN + 56
TAG_TABLE = _HWPTAG_BEGIN + 61

# 컨트롤 ID — MAKE_4CHID('t','b','l',' ') (명세 4.3.9)
_CTRL_ID_TABLE = (ord("t") << 24) | (ord("b") << 16) | (ord("l") << 8) | ord(" ")

# 제어 문자 형식 (명세 표 6). 인라인·확장 컨트롤은 8 WCHAR를 차지한다.
_INLINE_CTRL = {4, 5, 6, 7, 8, 9, 19, 20}
_EXTENDED_CTRL = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}

# FileHeader 속성 비트 (명세 표 3)
_FLAG_COMPRESSED = 1 << 0
_FLAG_PASSWORD = 1 << 1
_FLAG_DISTRIBUTION = 1 << 2


# ── 공통: 결과 조립 ──────────────────────────────────────────────────────────

def _failed(source_file: str, message: str) -> dict:
    return {"source_file": source_file, "status": "failed", "error": message, "pages": []}


def _result(source_file: str, method: str, sections: list[list[str]]) -> dict:
    sections = [[b.strip() for b in blocks if b.strip()] for blocks in sections]
    sections = [blocks for blocks in sections if blocks]
    if not sections:
        return _failed(source_file, "문서에서 텍스트를 찾지 못했습니다.")
    return {
        "source_file": source_file,
        "status": "success",
        "error": None,
        "pages": [{
            "page": i,
            "text": "\n\n".join(blocks),
            "method": method,
            "ocr_confidence": None,
            "flagged": False,
        } for i, blocks in enumerate(sections, start=1)],
    }


def _cell_text(text: str) -> str:
    """Markdown 표 셀 안에서 줄바꿈과 | 가 표를 깨뜨리지 않게 정리한다."""
    return " ".join(text.split()).replace("|", "\\|")


def _rows_to_markdown(grid: list[list[str]]) -> str:
    if not grid:
        return ""
    ncols = max(len(r) for r in grid)
    if ncols == 0:
        return ""
    rows = [[_cell_text(c) for c in r] + [""] * (ncols - len(r)) for r in grid]
    if not any(any(c for c in r) for r in rows):
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * ncols) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _place_cells(cells: list[tuple[int, int, str]], nrows: int, ncols: int) -> list[list[str]]:
    """(행, 열, 텍스트) 목록을 2차원 격자로 배치한다. 주소가 범위를 벗어나면 격자를 넓힌다."""
    if cells:
        nrows = max([nrows] + [r + 1 for r, _, _ in cells])
        ncols = max([ncols] + [c + 1 for _, c, _ in cells])
    grid = [["" for _ in range(ncols)] for _ in range(nrows)]
    for r, c, text in cells:
        grid[r][c] = (grid[r][c] + " " + text).strip() if grid[r][c] else text
    return grid


# ── HWP 5.0 (바이너리) ────────────────────────────────────────────────────────

def _read_records(data: bytes):
    """레코드 스트림을 (tag, level, payload)로 차례대로 돌려준다 (명세 4.1)."""
    pos, n = 0, len(data)
    while pos + 4 <= n:
        (header,) = struct.unpack_from("<I", data, pos)
        pos += 4
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if pos + 4 > n:
                break
            (size,) = struct.unpack_from("<I", data, pos)
            pos += 4
        yield tag, level, data[pos:pos + size]
        pos += size


def _decode_para_text(payload: bytes) -> str:
    """PARA_TEXT의 WCHAR 배열에서 제어 문자를 처리해 사람이 읽는 글자만 남긴다 (명세 표 6)."""
    count = len(payload) // 2
    units = struct.unpack_from(f"<{count}H", payload)
    out: list[str] = []
    run: list[int] = []

    def flush():
        if run:
            out.append(struct.pack(f"<{len(run)}H", *run).decode("utf-16-le", errors="replace"))
            run.clear()

    i = 0
    while i < count:
        ch = units[i]
        if ch >= 32:
            run.append(ch)
            i += 1
            continue
        flush()
        if ch == 9:                      # 탭 (인라인)
            out.append("\t")
            i += 8
        elif ch in _INLINE_CTRL or ch in _EXTENDED_CTRL:
            i += 8
        elif ch == 10:                   # 한 줄 끝
            out.append("\n")
            i += 1
        elif ch == 24:                   # 하이픈
            out.append("-")
            i += 1
        elif ch in (30, 31):             # 묶음 빈칸, 고정폭 빈칸
            out.append(" ")
            i += 1
        else:                            # 0, 13(문단 끝), 25~29(예약)
            i += 1
    flush()
    return "".join(out)


class _HwpTable:
    def __init__(self, level: int):
        self.level = level
        self.nrows = 0
        self.ncols = 0
        self.cells: list[tuple[int, int, list[str]]] = []
        self.current: list[str] | None = None

    def set_shape(self, payload: bytes):
        if len(payload) >= 8:
            self.nrows, self.ncols = struct.unpack_from("<HH", payload, 4)

    def start_cell(self, payload: bytes):
        """셀 리스트 = 문단 리스트 헤더 + 셀 속성(열, 행 …) (명세 표 79, 80).
        명세의 문단 리스트 헤더는 6바이트로 적혀 있지만 실제 파일에는 8바이트로 저장된 경우가
        있어서, 두 위치를 모두 보고 표 크기 안에 들어오는 주소를 쓴다."""
        addr = None
        for off in (8, 6):
            if len(payload) >= off + 4:
                col, row = struct.unpack_from("<HH", payload, off)
                if (not self.ncols or col < self.ncols) and (not self.nrows or row < self.nrows):
                    addr = (row, col)
                    break
        if addr is None:
            width = max(self.ncols, 1)
            addr = (len(self.cells) // width, len(self.cells) % width)
        self.current = []
        self.cells.append((addr[0], addr[1], self.current))

    def add_text(self, text: str):
        if self.current is None:
            self.current = []
            self.cells.append((0, len(self.cells), self.current))
        self.current.append(text)

    def to_markdown(self) -> str:
        cells = [(r, c, " ".join(t for t in texts if t.strip())) for r, c, texts in self.cells]
        return _rows_to_markdown(_place_cells(cells, self.nrows, self.ncols))

    def to_plain(self) -> str:
        return " / ".join(" ".join(t) for _, _, t in self.cells if " ".join(t).strip())


def _parse_section_records(data: bytes) -> list[str]:
    """구역 하나의 레코드를 읽어 블록(문단 텍스트, 표 Markdown) 목록으로 돌려준다."""
    blocks: list[str] = []
    tables: list[_HwpTable] = []
    pending_ctrl_level: int | None = None   # 표 CTRL_HEADER 직후 TABLE 레코드를 기다리는 중

    def close_table():
        done = tables.pop()
        if tables:
            tables[-1].add_text(done.to_plain())   # 표 안의 표는 바깥 셀의 글자로 넣는다
        else:
            blocks.append(done.to_markdown())

    for tag, level, payload in _read_records(data):
        # 표 영역(표 CTRL_HEADER보다 깊은 레코드)이 끝났으면 표를 마무리한다
        while tables and level <= tables[-1].level:
            close_table()

        if tag == TAG_PARA_TEXT:
            text = _decode_para_text(payload)
            if tables:
                tables[-1].add_text(text)
            else:
                blocks.append(text)
        elif tag == TAG_CTRL_HEADER:
            ctrl_id = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
            pending_ctrl_level = level if ctrl_id == _CTRL_ID_TABLE else None
        elif tag == TAG_TABLE and pending_ctrl_level is not None:
            table = _HwpTable(pending_ctrl_level)
            table.set_shape(payload)
            tables.append(table)
            pending_ctrl_level = None
        elif tag == TAG_LIST_HEADER and tables and level == tables[-1].level + 1:
            tables[-1].start_cell(payload)

    while tables:
        close_table()
    return blocks


def parse_hwp(filepath: str, source_file: str) -> dict:
    """HWP 5.0 바이너리 문서를 파서 계약 형식으로 변환한다."""
    try:
        if not olefile.isOleFile(filepath):
            return _failed(source_file, "HWP 5.0 형식이 아닙니다(손상되었거나 HWP 3.0 이하 문서일 수 있습니다).")
        with olefile.OleFileIO(filepath) as ole:
            if not ole.exists("FileHeader"):
                return _failed(source_file, "HWP 파일 헤더가 없습니다.")
            header = ole.openstream("FileHeader").read()
            if not header.startswith(b"HWP Document File") or len(header) < 40:
                return _failed(source_file, "HWP 문서 서명이 맞지 않습니다.")
            (props,) = struct.unpack_from("<I", header, 36)
            if props & _FLAG_PASSWORD:
                return _failed(source_file, "암호가 설정된 HWP 문서는 읽을 수 없습니다.")
            if props & _FLAG_DISTRIBUTION:
                return _failed(source_file, "배포용 HWP 문서는 내용이 암호화돼 있어 읽을 수 없습니다.")
            compressed = bool(props & _FLAG_COMPRESSED)

            entries = [e for e in ole.listdir()
                       if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")]
            entries.sort(key=lambda e: int(e[1][len("Section"):] or 0))
            if not entries:
                return _failed(source_file, "본문(BodyText) 구역을 찾지 못했습니다.")

            sections = []
            for entry in entries:
                raw = ole.openstream(entry).read()
                if compressed:
                    try:
                        raw = zlib.decompress(raw, -15)     # 헤더 없는 deflate
                    except zlib.error:
                        raw = zlib.decompress(raw)          # zlib 헤더가 붙은 경우
                sections.append(_parse_section_records(raw))
        return _result(source_file, "hwp", sections)
    except Exception as e:
        return _failed(source_file, f"HWP 파싱 오류: {e}")


# ── HWPX (OWPML, KS X 6101) ──────────────────────────────────────────────────

def _local(tag) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def _hwpx_t_text(t_elem) -> str:
    """<hp:t> 안의 글자. 탭·줄바꿈은 자식 요소로 들어 있다."""
    parts = [t_elem.text or ""]
    for child in t_elem:
        name = _local(child.tag)
        if name == "tab":
            parts.append("\t")
        elif name == "lineBreak":
            parts.append("\n")
        parts.append(child.tail or "")
    return "".join(parts)


def _hwpx_all_text(elem) -> str:
    """하위 요소 전체에서 <hp:t> 글자를 문서 순서대로 모은다(셀, 글상자, 각주 등)."""
    texts = (_hwpx_t_text(t) for t in elem.iter() if _local(t.tag) == "t")
    return " ".join(s for s in texts if s.strip())


def _hwpx_table(tbl) -> str:
    """표는 문단 > run > tbl 안에 들어 있다. 셀 주소(cellAddr)로 격자에 놓고 Markdown 표로 만든다.
    표 안의 표는 바깥 셀의 글자로 함께 들어간다(_hwpx_all_text가 하위 전체를 읽음)."""
    cells = []
    for tr in (c for c in tbl if _local(c.tag) == "tr"):
        for tc in (c for c in tr if _local(c.tag) == "tc"):
            addr = next((c for c in tc if _local(c.tag) == "cellAddr"), None)
            row = int(addr.get("rowAddr", 0)) if addr is not None else 0
            col = int(addr.get("colAddr", 0)) if addr is not None else len(cells)
            cells.append((row, col, _hwpx_all_text(tc)))
    grid = _place_cells(cells, int(tbl.get("rowCnt", 0) or 0), int(tbl.get("colCnt", 0) or 0))
    return _rows_to_markdown(grid)


def _hwpx_paragraph(p) -> list[str]:
    blocks: list[str] = []
    texts: list[str] = []
    for run in (c for c in p if _local(c.tag) == "run"):
        for child in run:
            name = _local(child.tag)
            if name == "t":
                texts.append(_hwpx_t_text(child))
            elif name == "tbl":
                blocks.append("".join(texts))
                texts = []
                blocks.append(_hwpx_table(child))
            elif name not in ("secPr", "ctrl"):
                inner = _hwpx_all_text(child)   # 글상자·그림 캡션 등
                if inner:
                    texts.append(" " + inner + " ")
    blocks.append("".join(texts))
    return blocks


def parse_hwpx(filepath: str, source_file: str) -> dict:
    """HWPX(OWPML) 문서를 파서 계약 형식으로 변환한다."""
    try:
        with zipfile.ZipFile(filepath) as zf:
            names = [n for n in zf.namelist() if n.startswith("Contents/section") and n.endswith(".xml")]
            names.sort(key=lambda n: int("".join(ch for ch in n.rsplit("/", 1)[-1] if ch.isdigit()) or 0))
            if not names:
                return _failed(source_file, "HWPX 본문(Contents/section*.xml)을 찾지 못했습니다.")
            parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
            sections = []
            for name in names:
                root = etree.fromstring(zf.read(name), parser)
                blocks: list[str] = []
                for p in (c for c in root if _local(c.tag) == "p"):
                    blocks.extend(_hwpx_paragraph(p))
                sections.append(blocks)
        return _result(source_file, "hwpx", sections)
    except zipfile.BadZipFile:
        return _failed(source_file, "유효한 HWPX 파일이 아닙니다(압축 형식 오류).")
    except etree.XMLSyntaxError as e:
        return _failed(source_file, f"HWPX 본문 XML 오류: {e}")
    except Exception as e:
        return _failed(source_file, f"HWPX 파싱 오류: {e}")

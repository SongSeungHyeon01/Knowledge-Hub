from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ParseStatus(str, Enum):
    OK = "ok"
    FLAGGED = "flagged"   # EasyOCR confidence < 0.7 페이지 포함
    FAILED = "failed"     # 파싱 실패


@dataclass
class PageResult:
    page_num: int         # 1-based (XLSX: 시트 순번, DOCX/HWP/HWPX: 1)
    markdown: str
    raw_text: str = ""    # 윤준서 PDF 파이프라인 중간 출력용


@dataclass
class ParseResult:
    source_file: str
    status: ParseStatus
    error: Optional[str] = None
    pages: list[PageResult] = field(default_factory=list)

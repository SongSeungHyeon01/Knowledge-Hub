# core/docling_parser.py — 멀티포맷 파서
# core/parser.py는 수정하지 않습니다 — PDF는 parse_pdf를 직접 사용하세요
#
# 포맷별 파서:
#   PDF          → main.py에서 core/parser.py parse_pdf 직접 호출 (여기서 처리 안 함)
#   DOCX         → python-docx          (pip install python-docx)
#   PPTX / PPT   → python-pptx          (pip install python-pptx)
#   XLSX / XLS   → openpyxl             (pip install openpyxl)
#   HWP          → pyhwp(hwp5txt) CLI → LibreOffice 순서로 시도
#   HWPX         → ZIP 내부 XML 직접 파싱 (추가 패키지 불필요)
#   TXT / MD     → 직접 읽기 (추가 패키지 불필요)
#   PNG / JPG    → Docling OCR 설치 시 지원 (현재: 안내 메시지 반환)
#
# Docling 설치 시 자동으로 Docling 파이프라인으로 전환됩니다 (더 정확한 OCR/레이아웃 인식)
#
# 반환 형식 (core/parser.py parse_pdf 와 동일):
#   {
#     "source_file": str,
#     "status":      "success" | "failed",
#     "error":       str or None,
#     "pages":       [
#       { "page_num": int, "text": str, "flagged": bool, "ocr_confidence": float }
#     ]
#   }

import os
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict
from typing import Optional

OCR_THRESHOLD = 0.7


def parse_document(filepath: str, source_file: str, file_type: Optional[str] = None) -> dict:
    """
    파일 경로를 받아 포맷에 맞게 파싱하고 페이지별 결과를 반환합니다.
    PDF는 main.py에서 parse_pdf를 직접 호출하므로 여기서는 처리하지 않습니다.
    """
    ext = Path(filepath).suffix.lower()

    # Docling이 설치돼 있으면 Docling 파이프라인으로 전환합니다
    if _has_docling() and ext in ('.pdf', '.docx', '.pptx', '.ppt', '.xlsx', '.xls', '.png', '.jpg', '.jpeg'):
        return _parse_with_docling(filepath, source_file)

    try:
        if ext in ('.docx',):
            return _parse_docx(filepath, source_file)
        elif ext in ('.pptx', '.ppt'):
            return _parse_pptx(filepath, source_file)
        elif ext in ('.xlsx', '.xls'):
            return _parse_xlsx(filepath, source_file)
        elif ext == '.hwp':
            return _parse_hwp(filepath, source_file)
        elif ext == '.hwpx':
            return _parse_hwpx(filepath, source_file)
        elif ext in ('.txt', '.md'):
            return _parse_text(filepath, source_file)
        elif ext in ('.png', '.jpg', '.jpeg'):
            return _fail(
                source_file,
                "이미지 OCR은 Docling 설치 후 지원됩니다. "
                "설치: pip install docling"
            )
        elif ext == '.pdf':
            # PDF가 여기까지 오면 core/parser.py를 호출합니다
            from core.parser import parse_pdf
            return parse_pdf(filepath=filepath, source_file=source_file)
        else:
            return _fail(source_file, f"지원하지 않는 파일 형식: {ext}")
    except Exception as e:
        return _fail(source_file, f"파싱 중 예외 발생: {e}")


def _has_docling() -> bool:
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


# ── DOCX 파서 ────────────────────────────────────────────────────────────────

def _parse_docx(filepath: str, source_file: str) -> dict:
    """python-docx로 DOCX 파일을 파싱합니다."""
    try:
        from docx import Document as DocxDoc
    except ImportError:
        return _fail(source_file, "python-docx 미설치. pip install python-docx")

    try:
        doc = DocxDoc(filepath)

        # 단락 + 표 셀 텍스트 수집
        lines = []
        for para in doc.paragraphs:
            if para.text.strip():
                lines.append(para.text)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    lines.append(" | ".join(cells))

        text = "\n".join(lines)
        return _one_page(source_file, text)
    except Exception as e:
        return _fail(source_file, f"DOCX 파싱 오류: {e}")


# ── PPTX 파서 ────────────────────────────────────────────────────────────────

def _parse_pptx(filepath: str, source_file: str) -> dict:
    """python-pptx로 PPTX/PPT 파일을 슬라이드별로 파싱합니다."""
    try:
        from pptx import Presentation
    except ImportError:
        return _fail(source_file, "python-pptx 미설치. pip install python-pptx")

    try:
        prs   = Presentation(filepath)
        pages = []
        for i, slide in enumerate(prs.slides, start=1):
            lines = []
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    lines.append(shape.text.strip())
            pages.append({
                "page_num":       i,
                "text":           "\n".join(lines),
                "flagged":        False,
                "ocr_confidence": 1.0,
            })

        if not pages:
            return _fail(source_file, "슬라이드에서 텍스트를 추출하지 못했습니다")
        return {"source_file": source_file, "status": "success", "error": None, "pages": pages}
    except Exception as e:
        return _fail(source_file, f"PPTX 파싱 오류: {e}")


# ── XLSX 파서 ────────────────────────────────────────────────────────────────

def _parse_xlsx(filepath: str, source_file: str) -> dict:
    """openpyxl로 XLSX/XLS 파일을 시트별로 파싱합니다."""
    try:
        import openpyxl
    except ImportError:
        return _fail(source_file, "openpyxl 미설치. pip install openpyxl")

    try:
        wb    = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        pages = []
        for i, sheet in enumerate(wb.worksheets, start=1):
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            pages.append({
                "page_num":       i,
                "text":           f"[시트: {sheet.title}]\n" + "\n".join(rows),
                "flagged":        False,
                "ocr_confidence": 1.0,
            })

        if not pages:
            return _fail(source_file, "시트에서 내용을 추출하지 못했습니다")
        return {"source_file": source_file, "status": "success", "error": None, "pages": pages}
    except Exception as e:
        return _fail(source_file, f"XLSX 파싱 오류: {e}")


# ── HWP 파서 ─────────────────────────────────────────────────────────────────

def _parse_hwp(filepath: str, source_file: str) -> dict:
    """HWP 파싱: pyhwp(hwp5txt) 1차 → LibreOffice → DOCX → python-docx 2차"""
    # 1차: pyhwp CLI
    try:
        proc = subprocess.run(
            ["hwp5txt", filepath],
            capture_output=True, text=True, timeout=30, encoding="utf-8"
        )
        text = proc.stdout.strip()
        if text:
            return _one_page(source_file, text)
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    # 2차: LibreOffice headless → DOCX → python-docx
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "docx",
                 "--outdir", tmpdir, filepath],
                capture_output=True, timeout=120
            )
            docx_path = os.path.join(tmpdir, Path(filepath).stem + ".docx")
            if os.path.exists(docx_path):
                result = _parse_docx(docx_path, source_file)
                if result["status"] == "success":
                    return result
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass

    return _fail(
        source_file,
        "HWP 파싱 실패: pyhwp(hwp5txt)와 LibreOffice가 모두 없거나 실패했습니다. "
        "설치: pip install pyhwp / brew install libreoffice"
    )


# ── HWPX 파서 ────────────────────────────────────────────────────────────────

def _parse_hwpx(filepath: str, source_file: str) -> dict:
    """HWPX 파싱: ZIP 내 Contents/section*.xml 에서 텍스트를 추출합니다."""
    try:
        texts: list[str] = []
        with zipfile.ZipFile(filepath, "r") as zf:
            section_files = sorted(
                n for n in zf.namelist()
                if n.startswith("Contents/section") and n.endswith(".xml")
            )
            for name in section_files:
                with zf.open(name) as f:
                    root = ET.parse(f).getroot()
                    for elem in root.iter():
                        if elem.text and elem.text.strip():
                            texts.append(elem.text.strip())

        if texts:
            return _one_page(source_file, "\n".join(texts))
        return _fail(source_file, "HWPX 파싱 실패: 텍스트를 추출하지 못했습니다")

    except zipfile.BadZipFile:
        return _fail(source_file, "HWPX 파싱 실패: 올바른 HWPX 파일이 아닙니다")
    except Exception as e:
        return _fail(source_file, f"HWPX 파싱 오류: {e}")


# ── 텍스트 파서 ──────────────────────────────────────────────────────────────

def _parse_text(filepath: str, source_file: str) -> dict:
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return _one_page(source_file, text)
    except Exception as e:
        return _fail(source_file, f"파일 읽기 오류: {e}")


# ── Docling 파서 (설치 시 자동 사용) ─────────────────────────────────────────

def _parse_with_docling(filepath: str, source_file: str) -> dict:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return _fail(source_file, "Docling 미설치")

    try:
        converter = DocumentConverter()
        result    = converter.convert(filepath)
        doc       = result.document

        page_texts: dict[int, list[str]] = defaultdict(list)
        for element, _level in doc.iterate_items():
            text = getattr(element, 'text', None)
            if not text:
                continue
            if element.prov:
                for prov in element.prov:
                    page_texts[getattr(prov, 'page_no', 1)].append(text)
            else:
                page_texts[1].append(text)

        pages = []
        if page_texts:
            for page_no in sorted(page_texts.keys()):
                confidence = _get_docling_page_confidence(result, page_no)
                pages.append({
                    "page_num":       page_no,
                    "text":           "\n".join(page_texts[page_no]),
                    "flagged":        confidence < OCR_THRESHOLD,
                    "ocr_confidence": confidence,
                })
        else:
            pages = [{"page_num": 1, "text": doc.export_to_markdown(), "flagged": False, "ocr_confidence": 1.0}]

        return {"source_file": source_file, "status": "success", "error": None, "pages": pages}
    except Exception as e:
        return _fail(source_file, f"Docling 파싱 오류: {e}")


def _get_docling_page_confidence(result, page_no: int) -> float:
    try:
        for page in result.pages:
            if getattr(page, 'page_no', None) != page_no:
                continue
            confs = [float(c.confidence) for c in getattr(page, 'cells', [])
                     if getattr(c, 'confidence', None) is not None]
            return sum(confs) / len(confs) if confs else 1.0
    except Exception:
        pass
    return 1.0


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────

def _one_page(source_file: str, text: str) -> dict:
    return {
        "source_file": source_file,
        "status":      "success",
        "error":       None,
        "pages":       [{"page_num": 1, "text": text, "flagged": False, "ocr_confidence": 1.0}],
    }

def _fail(source_file: str, error: str) -> dict:
    return {"source_file": source_file, "status": "failed", "error": error, "pages": []}

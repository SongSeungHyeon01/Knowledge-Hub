"""docling_parser.py — Docling 통합 파서 (PDF·DOCX·PPTX·XLSX 등)

[2026-07-28 교체] 예전에는 두 갈래로 나뉘어 있었다:
  - PDF: core/parser.py (pdfplumber + camelot-py + EasyOCR + img2table)
  - DOCX/PPTX/XLSX/HWP/HWPX: hwp_postprocess/ (LibreOffice headless 경유)
이 파일이 그 두 갈래를 Docling 하나로 통합한다. HWP/HWPX는 Docling이 지원하지
않아(2026-07-28 재확인, docling-project.github.io/docling/usage/supported_formats/)
이 교체와 함께 지원 자체를 포기한다 — main.py의 SUPPORTED_EXTENSIONS에서 빠지고
업로드 단계에서 거부된다.

파서 출력 계약(core/office_adapter.py와 동일):
    {"source_file", "status": "success"|"failed", "error", "pages": [
        {"page", "text", "method", "ocr_confidence", "flagged"}
    ]}

페이지 판정 근거:
  - doc.pages는 페이지 크기/이미지만 들고 있고 텍스트가 없다 — 실제 텍스트는
    doc.texts(평평한 리스트)에 있고 각 항목의 prov[0].page_no로 소속 페이지를 안다.
  - DOCX처럼 고정 페이지 개념이 없는 형식은 prov가 없거나 page_no가 None이라
    전부 1페이지로 묶는다(기존 hwp_postprocess도 DOCX를 "페이지 루프 없이 문서
    전체 한 번에 처리"했으므로 동일한 취급 — 회귀 아님).
  - 저신뢰 페이지 플래그는 예전 OCR_CONFIDENCE_THRESHOLD(0.7, 원시 float)를
    Docling의 ConfidenceReport 등급(QualityGrade)으로 대체한다 — Docling 공식
    문서가 "원시 점수는 버전마다 바뀔 수 있으니 등급을 쓰라"고 명시하기 때문.
"""

import math

from docling.datamodel.base_models import QualityGrade
from docling.document_converter import DocumentConverter

# 이 등급 이하면 관리자 확인 대상(flagged=True) — 기존 OCR_CONFIDENCE_THRESHOLD와
# 동일한 취지(저신뢰 자동 인식 결과를 사람이 검토하게 함).
_FLAG_GRADES = {QualityGrade.POOR, QualityGrade.FAIR}

_converter = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        _converter = DocumentConverter()
    return _converter


def _safe_score(value) -> float | None:
    """NaN(해당 문서에 OCR/표가 없어서 측정 자체가 안 된 경우)이면 None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return float(value)


def parse_with_docling(filepath: str, source_file: str) -> dict:
    try:
        result = _get_converter().convert(filepath, raises_on_error=False)
    except Exception as e:
        return {"source_file": source_file, "status": "failed", "error": f"Docling 변환 오류: {e}", "pages": []}

    if result.status.name not in ("SUCCESS", "PARTIAL_SUCCESS"):
        detail = "; ".join(str(e) for e in result.errors) if result.errors else "알 수 없는 오류"
        return {"source_file": source_file, "status": "failed", "error": detail, "pages": []}

    doc = result.document
    page_conf = result.confidence.pages if result.confidence else {}

    texts_by_page: dict[int, list[str]] = {}
    for t in doc.texts:
        pno = (t.prov[0].page_no if t.prov else None) or 1
        texts_by_page.setdefault(pno, []).append(t.text)
    for tbl in doc.tables:
        pno = (tbl.prov[0].page_no if tbl.prov else None) or 1
        try:
            texts_by_page.setdefault(pno, []).append(tbl.export_to_markdown(doc=doc))
        except Exception:
            pass  # 표 마크다운 변환 실패는 본문 텍스트에는 영향 없으니 조용히 건너뜀

    if not texts_by_page:
        return {"source_file": source_file, "status": "failed", "error": "본문을 추출하지 못했습니다", "pages": []}

    pages = []
    for pno in sorted(texts_by_page):
        conf = page_conf.get(pno)
        grade = conf.mean_grade if conf is not None else QualityGrade.UNSPECIFIED
        pages.append({
            "page": pno,
            "text": "\n".join(texts_by_page[pno]),
            "method": "docling",
            "ocr_confidence": _safe_score(conf.ocr_score) if conf is not None else None,
            "flagged": grade in _FLAG_GRADES,
        })

    return {"source_file": source_file, "status": "success", "error": None, "pages": pages}

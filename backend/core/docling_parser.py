"""docling_parser.py — Docling 통합 파서 (PDF·DOCX·PPTX·XLSX 등)

[2026-07-28 교체] 예전에는 두 갈래로 나뉘어 있었다:
  - PDF: core/parser.py (pdfplumber + camelot-py + EasyOCR + img2table)
  - DOCX/PPTX/XLSX/HWP/HWPX: hwp_postprocess/ (LibreOffice headless 경유)
이 파일이 그 두 갈래를 Docling 하나로 통합한다. HWP/HWPX는 Docling이 지원하지
않아(2026-07-28 재확인, docling-project.github.io/docling/usage/supported_formats/)
이 파일로 오지 않는다 — [2026-10-24]부터 core/hwp_parser.py가 따로 처리한다.

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

[2026-10-24 수정] 측정(eval/run_eval.py)에서 드러난 두 가지를 고쳤다.
  1) OCR 언어: 언어를 지정하지 않으면 OCR 엔진이 중국어+영어 모델을 써서, 스캔본의 한국어를
     하나도 읽지 못했다(EasyOCR 시절 포함, 한국어 재현율 0.000). RapidOCR에 PP-OCRv5 한국어
     인식 모델("korean")을 지정했다 — 같은 스캔본에서 글자 재현율 0.269 → 0.690(한국어 0.595).
     Docling의 표준 언어 표기 "ko"는 이 버전에서 모델로 연결되지 않아 RapidOCR 고유 코드를 쓴다.
  2) 플래그 기준: 예전엔 페이지 평균 등급(mean_grade)을 봤는데, 평균에는 OCR 말고도 글자 레이어
     추출 점수(parse_score)·레이아웃 점수가 섞여 있어서, 스캔본이 아닌 일반 PDF가 통째로 "확인
     필요"가 되고 검색 색인에서 빠졌다(실측: 배포 브리핑·작업정리서 PDF). 이제 원래 의도대로
     "글자 레이어가 없는(OCR로만 읽은) 페이지의 OCR 등급"만 본다. 그리고 글자 레이어는 있는데
     Docling이 글자를 깨뜨려 읽은 페이지(parse_score POOR — 특정 글꼴에서 'platform'이
     'plat테orm'이 되는 식)는 pypdfium2로 그 페이지 글자를 다시 읽어 쓴다.
"""

import math
import os

from docling.datamodel.base_models import InputFormat, QualityGrade
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

# 이 등급 이하면 관리자 확인 대상(flagged=True) — 기존 OCR_CONFIDENCE_THRESHOLD와
# 동일한 취지(저신뢰 자동 인식 결과를 사람이 검토하게 함).
_FLAG_GRADES = {QualityGrade.POOR, QualityGrade.FAIR}

# RapidOCR 인식 언어(PP-OCR 코드). 한국어 모델은 영문·숫자도 함께 읽는다(2026-10-24 실측).
OCR_LANG = os.environ.get("OCR_LANG", "korean")

_converter = None


def _get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        pdf_options = PdfPipelineOptions(ocr_options=RapidOcrOptions(lang=[OCR_LANG]))
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)}
        )
    return _converter


def _grade(score) -> QualityGrade:
    """Docling PageConfidenceScores._score_to_grade와 같은 구간(0.5 / 0.8 / 0.9).
    NaN(그 단계가 아예 안 돈 경우)은 UNSPECIFIED."""
    if score is None or (isinstance(score, float) and math.isnan(score)):
        return QualityGrade.UNSPECIFIED
    if score < 0.5:
        return QualityGrade.POOR
    if score < 0.8:
        return QualityGrade.FAIR
    if score < 0.9:
        return QualityGrade.GOOD
    return QualityGrade.EXCELLENT


def _pdfium_page_text(filepath: str, page_no: int) -> str:
    """Docling이 글자를 깨뜨린 페이지를 pypdfium2로 다시 읽는다(page_no는 1부터)."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(filepath)
        try:
            if 1 <= page_no <= len(pdf):
                return pdf[page_no - 1].get_textpage().get_text_range().strip()
        finally:
            pdf.close()
    except Exception as e:
        print(f"[docling] pypdfium2 재추출 실패(page {page_no}): {e}")
    return ""


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

    is_pdf = filepath.lower().endswith(".pdf")
    pages = []
    for pno in sorted(texts_by_page):
        conf = page_conf.get(pno)
        parse_grade = _grade(conf.parse_score) if conf is not None else QualityGrade.UNSPECIFIED
        ocr_grade = _grade(conf.ocr_score) if conf is not None else QualityGrade.UNSPECIFIED
        text, method = "\n".join(texts_by_page[pno]), "docling"

        # 글자 레이어를 Docling이 깨뜨려 읽은 페이지 → pypdfium2로 다시 읽은 글자를 쓴다
        if is_pdf and parse_grade == QualityGrade.POOR:
            fallback = _pdfium_page_text(filepath, pno)
            if fallback:
                text, method = fallback, "pdfium"

        # 글자 레이어가 없는(OCR로만 읽은) 페이지의 OCR 등급이 낮을 때만 관리자 확인 대상
        ocr_only = parse_grade == QualityGrade.UNSPECIFIED and ocr_grade != QualityGrade.UNSPECIFIED
        pages.append({
            "page": pno,
            "text": text,
            "method": method,
            "ocr_confidence": _safe_score(conf.ocr_score) if conf is not None else None,
            "flagged": ocr_only and ocr_grade in _FLAG_GRADES,
        })

    return {"source_file": source_file, "status": "success", "error": None, "pages": pages}

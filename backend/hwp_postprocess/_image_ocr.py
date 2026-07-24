"""EasyOCR + img2table 이미지 텍스트·표 추출 헬퍼 (DOCX embedded 이미지용)"""
import os
import threading

_easyocr_reader = None
_easyocr_lock = threading.Lock()

_i2t_ocr = None
_i2t_lock = threading.Lock()


def _get_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        with _easyocr_lock:
            if _easyocr_reader is None:
                import easyocr
                model_dir = os.getenv("EASYOCR_MODEL_DIR", "/data/easyocr_models")
                _easyocr_reader = easyocr.Reader(
                    ["en", "ko"],
                    model_storage_directory=model_dir,
                    gpu=False,
                )
    return _easyocr_reader


def _get_i2t_ocr():
    global _i2t_ocr
    if _i2t_ocr is None:
        with _i2t_lock:
            if _i2t_ocr is None:
                from img2table.ocr import EasyOCR as I2TOCR
                model_dir = os.getenv("EASYOCR_MODEL_DIR", "/data/easyocr_models")
                _i2t_ocr = I2TOCR(
                    lang=["en", "ko"],
                    kw={"model_storage_directory": model_dir, "gpu": False},
                )
    return _i2t_ocr


def extract_image_markdown(image_bytes: bytes) -> str:
    """
    이미지 bytes → Markdown 문자열 반환.
    표 구조가 감지되면 Markdown 테이블, 아니면 OCR 텍스트 반환.
    추출 실패 시 빈 문자열 반환.
    """
    table_md = _try_extract_tables(image_bytes)
    if table_md:
        return table_md
    return _extract_plain_text(image_bytes)


def _try_extract_tables(image_bytes: bytes) -> str:
    try:
        from img2table.document import Image as I2TImage

        doc = I2TImage(src=image_bytes)
        tables = doc.extract_tables(
            ocr=_get_i2t_ocr(),
            implicit_rows=True,
            borderless_tables=False,
        )
        # Image 문서는 page 0 에 결과가 들어옴
        extracted = tables.get(0, [])
        if not extracted:
            return ""

        blocks = []
        for t in extracted:
            md = _df_to_markdown(t.df)
            if md:
                blocks.append(md)
        return "\n\n".join(blocks)
    except Exception:
        return ""


def _extract_plain_text(image_bytes: bytes) -> str:
    try:
        results = _get_easyocr().readtext(image_bytes, detail=1)
        # confidence 0.5 미만 결과는 제외
        lines = [r[1] for r in results if r[2] >= 0.5]
        return "\n".join(lines)
    except Exception:
        return ""


def _df_to_markdown(df) -> str:
    try:
        if df is None or df.empty:
            return ""
        rows = [list(df.columns)] + df.values.tolist()
        max_cols = max(len(r) for r in rows)
        rows = [list(r) + [""] * (max_cols - len(r)) for r in rows]

        def cell(v):
            return str(v).replace("|", "\\|").replace("\n", " ")

        lines = ["| " + " | ".join(cell(c) for c in rows[0]) + " |"]
        lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(cell(c) for c in row) + " |")
        return "\n".join(lines)
    except Exception:
        return ""

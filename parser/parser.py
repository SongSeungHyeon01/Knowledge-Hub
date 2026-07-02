# parser.py — PDF 파이프라인 메인 모듈
# 단계별로 함수를 추가해 나간다.
#   2단계: classify_pdf      — 텍스트형/스캔형 판별
#   3단계: extract_text_pages — 텍스트형 PDF 본문 추출
#   4단계: extract_ocr_pages  — 스캔형 PDF OCR 인식 + 신뢰도 계산
import io

import fitz         # PyMuPDF (extract_ocr_pages의 페이지->이미지 변환에 아직 사용 — STEP 5 보고 참고)
import pdfplumber   # PDF 텍스트 추출 (MIT) — classify_pdf()부터 순차 교체 중
import camelot      # PDF 표 추출 (MIT, 기본 백엔드 pdfium) — STEP 4에서 추가
import easyocr      # OCR 엔진
import numpy as np  # 이미지 배열 변환용
from PIL import Image as PILImage
from img2table.document import Image as Img2TableImage  # 스캔 이미지 내 표 인식 (MIT) — STEP 5에서 추가
from img2table.ocr import EasyOCR as Img2TableEasyOCR

# 페이지에 이 글자 수 이상이면 "텍스트형"으로 판별한다.
# 너무 짧으면 스캔 PDF의 메타데이터 잡음(공백·제어문자)일 수 있어서 10자로 걸러낸다.
TEXT_THRESHOLD = 10


def classify_pdf(filepath: str) -> dict:
    """
    2단계: PDF 파일을 열어 각 페이지가 텍스트형인지 스캔형인지 판별한다.
    텍스트 추출이나 OCR은 하지 않는다 — 판별만.

    반환값:
    {
        "status": "success",
        "error": None,
        "pages": [
            {"page": 1, "type": "text"},   # 텍스트 레이어 있음 → 3단계로
            {"page": 2, "type": "scan"},   # 텍스트 레이어 없음 → 4단계(OCR)로
        ]
    }
    오류 시:
    {
        "status": "failed",
        "error": "오류 메시지",
        "pages": []
    }
    """
    try:
        with pdfplumber.open(filepath) as pdf:
            # 페이지가 없는 빈 PDF 걸러내기
            # (형식이 아예 PDF가 아니거나 손상된 파일은 pdfplumber.open() 자체에서 예외가 난다)
            if len(pdf.pages) == 0:
                raise ValueError("페이지가 없는 빈 PDF입니다.")

            pages = []
            for page_num, page in enumerate(pdf.pages, start=1):
                # extract_text()로 텍스트 레이어에서 글자를 읽어 본다.
                # 스캔 PDF는 여기서 빈 문자열(또는 None)이 나온다.
                text = (page.extract_text() or "").strip()
                page_type = "text" if len(text) >= TEXT_THRESHOLD else "scan"
                pages.append({"page": page_num, "type": page_type})

        return {"status": "success", "error": None, "pages": pages}

    except Exception as e:
        return {"status": "failed", "error": str(e), "pages": []}


def extract_text_pages(filepath: str, source_file: str = None, pages: list = None) -> dict:
    """
    3단계: 텍스트형 PDF에서 페이지별 본문 텍스트를 추출한다.
    classify_pdf()가 "text"로 판별한 PDF에 사용한다.
    OCR은 하지 않는다.

    source_file: 결과 객체에 기록할 상대 경로.
                 None이면 filepath를 그대로 사용.
    pages: 처리할 페이지 번호(1부터 시작) 목록. None이면 전체 페이지를 처리한다.
           parse_pdf()는 classify_pdf() 판별 결과 중 "text" 페이지 번호만 넘겨서 호출한다.

    표가 있는 페이지는 camelot으로 표 영역(bbox)을 감지해 그 안의 글자를
    pdfplumber 텍스트 추출에서 제외하고, 표는 마크다운 파이프 표로 변환해
    페이지 텍스트 끝에 붙인다 (설계서 2.1/2.2). 표가 없는 페이지는
    지금까지와 동일하게 페이지 전체를 그대로 추출한다.

    반환값 (CLAUDE.md 4번 출력 약속 형식):
    {
        "source_file": "경로/파일명.pdf",
        "status": "success",
        "error": null,
        "pages": [
            {
                "page": 1,
                "text": "추출된 날것 텍스트 (+ 표가 있으면 마크다운 표가 끝에 붙음)",
                "method": "text",
                "ocr_confidence": null,
                "flagged": false
            }
        ]
    }
    """
    if source_file is None:
        source_file = filepath

    try:
        with pdfplumber.open(filepath) as pdf:
            if len(pdf.pages) == 0:
                raise ValueError("페이지가 없는 빈 PDF입니다.")

            target_pages = pages if pages is not None else range(1, len(pdf.pages) + 1)

            result_pages = []
            for page_num in target_pages:
                page = pdf.pages[page_num - 1]
                text = _extract_page_text(filepath, page, page_num)
                result_pages.append({
                    "page": page_num,
                    "text": text,
                    "method": "text",
                    "ocr_confidence": None,   # 텍스트 직접 추출이라 신뢰도 없음
                    "flagged": False,          # 텍스트형은 항상 false
                })

        return {
            "source_file": source_file,
            "status": "success",
            "error": None,
            "pages": result_pages,
        }

    except Exception as e:
        return {
            "source_file": source_file,
            "status": "failed",
            "error": str(e),
            "pages": [],
        }


def _extract_page_text(filepath: str, page, page_num: int) -> str:
    """
    한 페이지의 텍스트를 뽑되, 표가 있으면 표 영역을 제외한 텍스트 + 마크다운 표를 합쳐 반환한다.

    1. camelot으로 이 페이지에 표가 있는지 감지한다 (flavor="lattice" — 격자선 있는 표 기준.
       격자선이 없는 표는 이번 구현 범위 밖이다).
    2. 표가 없으면: 조기 반환 — pdfplumber로 페이지 전체를 그대로 추출하고 끝낸다.
       (표가 없는 페이지에서 카멜롯 처리를 더 하지 않기 위함 — 자원 절약)
    3. 표가 있으면: camelot 좌표(bbox)를 pdfplumber 좌표로 변환해 그 영역을 제외한
       텍스트를 뽑고, 표는 마크다운으로 변환해 페이지 끝에 붙인다.

    ── 성능 메모 (2026-07-03, STEP 4 리뷰에서 논의) ─────────────────────────
    표가 없는 페이지도 camelot 감지 자체(페이지당 약 0.2초)는 피할 수 없다.
    "파일 전체를 한 번에 camelot으로 스캔해 페이지별로 나누는" 방식이 더 빠르지만,
    지금은 일부러 하지 않는다:
      1) STEP 2~3에서 이미 검증을 마친 "페이지 단위 처리" 구조를 크게 흔드는
         리팩터링이라, 검증된 부분을 다시 위험에 빠뜨린다.
      2) 이 파싱은 문서 업로드 시점에 한 번 도는 배치 작업이라 사용자가 실시간으로
         기다리는 구간이 아니다 (검색 응답 5분 이내 같은 실사용 성공 기준과 무관).
      3) 실제 운영에서 이 부분이 병목이라고 측정된 적이 없다.
    실운영에서 느려짐이 실제로 확인되면 그때 다시 검토한다.
    ─────────────────────────────────────────────────────────────────────
    """
    try:
        tables = camelot.read_pdf(filepath, pages=str(page_num), flavor="lattice")
    except Exception:
        # camelot이 이 페이지에서 실패해도(예: 표처럼 보이는 선이 없음) 페이지 전체가
        # 죽으면 안 된다 — 표가 없는 것으로 간주하고 일반 텍스트 추출로 넘어간다.
        tables = []

    if len(tables) == 0:
        return page.extract_text() or ""

    # 좌표계 변환 (설계서 2.1 주의사항):
    #   camelot  = 왼쪽 아래가 원점, y는 위로 갈수록 커짐
    #   pdfplumber = 왼쪽 위가 원점, top은 아래로 갈수록 커짐
    #   => pdfplumber_top = page_height - camelot_y
    page_height = page.height

    # 표가 여러 개면 화면에 보이는 순서(위→아래)로 정렬한다 (camelot y1이 클수록 더 위쪽).
    ordered_tables = sorted(tables, key=lambda t: t._bbox[3], reverse=True)

    cropped = page
    markdown_blocks = []
    for table in ordered_tables:
        x0, y0, x1, y1 = table._bbox
        bbox_for_pdfplumber = (x0, page_height - y1, x1, page_height - y0)
        cropped = cropped.outside_bbox(bbox_for_pdfplumber)  # 표 영역 안의 글자를 제외
        markdown_blocks.append(_table_df_to_markdown(table.df))

    outside_text = cropped.extract_text() or ""
    # 정확한 위치 복원 대신, 표 밖 텍스트 뒤에 표들을 순서대로 붙인다 (설계서 2.1.4 — 허용된 방식).
    return (outside_text + "\n\n" + "\n\n".join(markdown_blocks)).strip()


def _table_df_to_markdown(df) -> str:
    """camelot이 뽑은 표(DataFrame)를 마크다운 파이프 표로 변환한다 (설계서 2.2).
    첫 행을 헤더로 승격하고, 빈 셀은 빈 문자열로, 셀 안 개행은 공백으로 바꿔
    파이프 표가 깨지지 않게 한다."""
    df = df.copy()
    df.columns = df.iloc[0]
    df = df.iloc[1:]
    df = df.fillna("").map(
        lambda cell: str(cell).replace("\n", " ").replace("\r", " ") if isinstance(cell, str) else cell
    )
    return df.to_markdown(index=False)


def extract_ocr_pages(filepath: str, source_file: str = None, pages: list = None) -> dict:
    """
    4단계: 스캔형 PDF에서 페이지별로 EasyOCR로 텍스트를 인식하고
    글자 수 가중 평균으로 페이지 대표 신뢰도를 계산한다.
    classify_pdf()가 "scan"으로 판별한 PDF에 사용한다.

    ── 글자 수 가중 평균이란? ──────────────────────────────────────────
    EasyOCR은 한 페이지에서 블록(줄/문장 단위 덩어리)을 여러 개 잡아내고,
    블록마다 신뢰도를 하나씩 준다.

    단순 평균은 짧은 블록과 긴 블록을 같은 비중으로 보는 문제가 있다.
    예) "가" 1글자짜리 블록(신뢰도 0.1)이 하나 있으면 평균이 크게 끌려내려간다.

    가중 평균은 "긴 블록일수록 더 중요하다"고 보고 글자 수를 비중으로 쓴다.
      가중합    = (블록A 글자수 × 신뢰도A) + (블록B 글자수 × 신뢰도B) + ...
      전체글자수 = 블록A 글자수 + 블록B 글자수 + ...
      가중평균   = 가중합 ÷ 전체글자수

    예시:
      블록A: "텍스트 레이어가 없고..." (21글자, 신뢰도 0.83)
      블록B: "OCR로만..."             ( 9글자, 신뢰도 0.54)
      가중합    = 21×0.83 + 9×0.54 = 17.43 + 4.86 = 22.29
      전체글자수 = 21 + 9 = 30
      가중평균   = 22.29 ÷ 30 = 0.743
    ────────────────────────────────────────────────────────────────────

    source_file: 결과 객체에 기록할 상대 경로. None이면 filepath 그대로 사용.
    pages: 처리할 페이지 번호(1부터 시작) 목록. None이면 전체 페이지를 처리한다.
           parse_pdf()는 classify_pdf() 판별 결과 중 "scan" 페이지 번호만 넘겨서 호출한다.
    """
    if source_file is None:
        source_file = filepath

    try:
        doc = fitz.open(filepath)

        if not doc.is_pdf:
            raise ValueError("PDF 형식이 아닌 파일입니다.")
        if len(doc) == 0:
            raise ValueError("페이지가 없는 빈 PDF입니다.")

        target_pages = pages if pages is not None else range(1, len(doc) + 1)

        # EasyOCR Reader 초기화 (한국어+영어, CPU 전용)
        # 모델 파일을 메모리에 올리는 작업이라 처음 한 번만 오래 걸린다.
        reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)

        # img2table도 내부적으로 EasyOCR을 쓰는데, 그냥 두면 자체적으로 reader를
        # 하나 더 만들어(약 1.8초 + 메모리 중복) 3GB RAM 제약에 불리하다.
        # img2table.ocr.EasyOCR.__init__은 self.lang / self.reader 두 속성만 쓰므로,
        # __new__로 생성자를 건너뛰고 위에서 이미 만든 reader를 그대로 주입해 재사용한다.
        img2table_ocr = Img2TableEasyOCR.__new__(Img2TableEasyOCR)
        img2table_ocr.lang = ['ko', 'en']
        img2table_ocr.reader = reader

        result_pages = []
        for page_num in target_pages:
            page = doc[page_num - 1]

            # ── 페이지 → 이미지 변환 ────────────────────────────────────
            # Matrix(2,2): 해상도를 원본의 2배로 키운다.
            # OCR은 해상도가 낮으면 글자를 못 알아보는 경우가 많다.
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)

            # PyMuPDF가 주는 픽셀 데이터를 numpy 배열로 변환한다.
            # pix.n == 채널 수 (보통 3=RGB, 드물게 4=RGBA)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            # EasyOCR은 RGB 3채널만 받으므로, RGBA이면 알파 채널을 제거한다.
            if pix.n == 4:
                img = img[:, :, :3]

            # ── EasyOCR 실행 ─────────────────────────────────────────────
            # raw: [(bbox, text, confidence), ...] 형태의 리스트
            # bbox는 좌표(사용 안 함), text는 인식된 글자, confidence는 신뢰도
            raw = reader.readtext(img)

            # ── 글자 수 가중 평균 계산 ───────────────────────────────────
            total_chars = sum(len(text) for _, text, _ in raw)

            if total_chars == 0:
                # 인식된 글자가 전혀 없는 페이지 (백지, 도면만 있는 경우)
                ocr_confidence = 0.0
                flagged = True   # 글자를 못 읽었으니 신뢰할 수 없음
                page_text = ""
            else:
                # 가중합 = 각 블록의 (글자수 × 신뢰도) 를 모두 더함
                weighted_sum = sum(len(text) * conf for _, text, conf in raw)
                ocr_confidence = weighted_sum / total_chars

                # ocr_confidence < 0.7 이면 못 믿을 페이지로 판정한다.
                # 정확히 0.7은 합격(false). "미만"이므로 < 를 쓴다.
                # bool()로 감싸는 이유: EasyOCR 신뢰도가 numpy.float64 타입이라
                # 비교 결과가 numpy.bool_로 나오면 json.dumps()가 오류를 낸다.
                # (신뢰도 계산·0.7 기준은 표 유무와 무관하게 이 페이지 전체 OCR 품질을
                #  나타내야 하므로, 아래 표 통합과 별개로 raw 전체 기준으로 그대로 유지한다.)
                flagged = bool(ocr_confidence < 0.7)

                # 표가 있으면 img2table로 인식해 마크다운으로 바꾸고, 표 안 블록은
                # 일반 텍스트에서 제외한다 (텍스트형 페이지의 2.1 설계와 같은 원칙을
                # 스캔 페이지에도 동일하게 적용 — 표 내용이 이중으로 들어가지 않게 함).
                page_text = _ocr_page_text_with_tables(raw, img, img2table_ocr)

            result_pages.append({
                "page": page_num,
                "text": page_text,
                "method": "ocr",
                # float()로 감싸는 이유: numpy.float64는 JSON 직렬화 불가
                "ocr_confidence": float(round(ocr_confidence, 4)),
                "flagged": flagged,
            })

        doc.close()
        return {
            "source_file": source_file,
            "status": "success",
            "error": None,
            "pages": result_pages,
        }

    except Exception as e:
        return {
            "source_file": source_file,
            "status": "failed",
            "error": str(e),
            "pages": [],
        }


def _ocr_page_text_with_tables(raw, img, img2table_ocr) -> str:
    """
    스캔 페이지의 OCR 결과에 표가 있으면 img2table로 표를 인식해 마크다운으로 바꾸고,
    표 영역 안에 있던 OCR 블록은 일반 텍스트에서 제외해 중복을 막는다 (설계서 2.3).

    camelot/pdfplumber 조합(설계서 2.1)과 달리, EasyOCR의 블록 좌표와 img2table의
    표 좌표는 애초에 같은 이미지의 픽셀 좌표계를 공유하므로 좌표 변환이 필요 없다.

    raw: reader.readtext(img)의 결과 [(bbox, text, confidence), ...]
    img: OCR에 사용한 numpy 이미지 (img2table 표 감지에도 그대로 재사용)
    img2table_ocr: reader를 재사용하도록 만든 img2table EasyOCR 래퍼
    """
    try:
        pil_img = PILImage.fromarray(img)
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        tables = Img2TableImage(src=buf.getvalue()).extract_tables(ocr=img2table_ocr, min_confidence=30)
    except Exception:
        # img2table이 실패해도(예: 표처럼 보이는 구조 없음) 페이지 전체가 죽으면 안 된다.
        tables = []

    if not tables:
        return "\n".join(text for _, text, _ in raw)

    def _block_center_in_any_table(bbox_points):
        xs = [p[0] for p in bbox_points]
        ys = [p[1] for p in bbox_points]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        return any(t.bbox.x1 <= cx <= t.bbox.x2 and t.bbox.y1 <= cy <= t.bbox.y2 for t in tables)

    outside_lines = [text for bbox, text, _ in raw if not _block_center_in_any_table(bbox)]
    # 화면에 보이는 순서(위→아래)로 표 정렬
    ordered_tables = sorted(tables, key=lambda t: t.bbox.y1)
    markdown_blocks = [_table_df_to_markdown(t.df) for t in ordered_tables]

    return ("\n".join(outside_lines) + "\n\n" + "\n\n".join(markdown_blocks)).strip()


def parse_pdf(filepath: str, source_file: str = None) -> dict:
    """
    최종 입구 함수: PDF 경로 하나를 받아 CLAUDE.md 4번 형식의 결과 객체를 반환한다.

    ── 내부 처리 흐름 ───────────────────────────────────────────────────
    로직을 직접 다시 구현하지 않고, 2~4단계 함수를 그대로 호출해 조립한다
    (같은 판별/추출 로직이 이 파일 여러 곳에 중복되는 것을 막기 위함).

    1. classify_pdf()로 페이지별 텍스트형/스캔형을 먼저 판별한다 (2단계).
       → 파일을 열 수 없으면 여기서 바로 status="failed"로 끝난다.
    2. "text"로 판별된 페이지 번호만 모아 extract_text_pages()에 넘긴다 (3단계).
    3. "scan"으로 판별된 페이지 번호만 모아 extract_ocr_pages()에 넘긴다 (4~5단계).
       → 스캔 페이지가 하나도 없으면 이 단계 자체를 건너뛰어 EasyOCR을 로드하지 않는다.
    4. 두 결과를 원래 페이지 번호 순서로 합쳐 반환한다.

    ⭐ EasyOCR Reader는 extract_ocr_pages() 내부에서, 스캔 페이지가 있을 때만
       한 번 초기화된다. 텍스트형 PDF만 있으면 EasyOCR이 아예 로드되지 않는다.
    ─────────────────────────────────────────────────────────────────────
    """
    if source_file is None:
        source_file = filepath

    classified = classify_pdf(filepath)
    if classified["status"] == "failed":
        return _failed_result(source_file, classified["error"])

    text_page_nums = [p["page"] for p in classified["pages"] if p["type"] == "text"]
    scan_page_nums = [p["page"] for p in classified["pages"] if p["type"] == "scan"]

    pages_by_num = {}

    if text_page_nums:
        text_result = extract_text_pages(filepath, source_file, pages=text_page_nums)
        if text_result["status"] == "failed":
            return _failed_result(source_file, text_result["error"])
        for page in text_result["pages"]:
            pages_by_num[page["page"]] = page

    if scan_page_nums:
        ocr_result = extract_ocr_pages(filepath, source_file, pages=scan_page_nums)
        if ocr_result["status"] == "failed":
            return _failed_result(source_file, ocr_result["error"])
        for page in ocr_result["pages"]:
            pages_by_num[page["page"]] = page

    ordered_pages = [pages_by_num[num] for num in sorted(pages_by_num)]

    return {
        "source_file": source_file,
        "status": "success",
        "error": None,
        "pages": ordered_pages,
    }


def _failed_result(source_file: str, err_msg: str) -> dict:
    """classify_pdf/extract_text_pages/extract_ocr_pages 중 어디서 실패하든 동일한 형식의 실패 결과를 만든다."""
    # fitz/pdfplumber가 파일을 열지 못할 때 나오는 영문 메시지를 한국어로 교체
    if "Failed to open" in err_msg or "cannot open" in err_msg.lower():
        err_msg = "PDF를 열 수 없음 (파일이 손상되었거나 형식이 올바르지 않습니다)"
    return {
        "source_file": source_file,
        "status": "failed",
        "error": err_msg,
        "pages": [],
    }

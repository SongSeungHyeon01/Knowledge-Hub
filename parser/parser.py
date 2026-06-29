# parser.py — PDF 파이프라인 메인 모듈
# 단계별로 함수를 추가해 나간다.
#   2단계: classify_pdf      — 텍스트형/스캔형 판별
#   3단계: extract_text_pages — 텍스트형 PDF 본문 추출
#   4단계: extract_ocr_pages  — 스캔형 PDF OCR 인식 + 신뢰도 계산
import fitz        # PyMuPDF
import easyocr     # OCR 엔진
import numpy as np # 이미지 배열 변환용

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
        doc = fitz.open(filepath)

        # PDF 형식이 아닌 파일(깨진 파일 등) 걸러내기
        if not doc.is_pdf:
            raise ValueError("PDF 형식이 아닌 파일입니다.")
        if len(doc) == 0:
            raise ValueError("페이지가 없는 빈 PDF입니다.")

        pages = []
        for page_num, page in enumerate(doc, start=1):
            # get_text()로 텍스트 레이어에서 글자를 읽어 본다.
            # 스캔 PDF는 여기서 빈 문자열이 나온다.
            text = page.get_text().strip()
            page_type = "text" if len(text) >= TEXT_THRESHOLD else "scan"
            pages.append({"page": page_num, "type": page_type})

        doc.close()
        return {"status": "success", "error": None, "pages": pages}

    except Exception as e:
        return {"status": "failed", "error": str(e), "pages": []}


def extract_text_pages(filepath: str, source_file: str = None) -> dict:
    """
    3단계: 텍스트형 PDF에서 페이지별 본문 텍스트를 추출한다.
    classify_pdf()가 "text"로 판별한 PDF에 사용한다.
    OCR은 하지 않는다.

    source_file: 결과 객체에 기록할 상대 경로.
                 None이면 filepath를 그대로 사용.

    반환값 (CLAUDE.md 4번 출력 약속 형식):
    {
        "source_file": "경로/파일명.pdf",
        "status": "success",
        "error": null,
        "pages": [
            {
                "page": 1,
                "text": "추출된 날것 텍스트",
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
        doc = fitz.open(filepath)

        if not doc.is_pdf:
            raise ValueError("PDF 형식이 아닌 파일입니다.")
        if len(doc) == 0:
            raise ValueError("페이지가 없는 빈 PDF입니다.")

        pages = []
        for page_num, page in enumerate(doc, start=1):
            # 날것 그대로 추출한다. 마크다운 변환 X (그건 ② 송승현 담당)
            text = page.get_text()
            pages.append({
                "page": page_num,
                "text": text,
                "method": "text",
                "ocr_confidence": None,   # 텍스트 직접 추출이라 신뢰도 없음
                "flagged": False,          # 텍스트형은 항상 false
            })

        doc.close()
        return {
            "source_file": source_file,
            "status": "success",
            "error": None,
            "pages": pages,
        }

    except Exception as e:
        return {
            "source_file": source_file,
            "status": "failed",
            "error": str(e),
            "pages": [],
        }


def extract_ocr_pages(filepath: str, source_file: str = None) -> dict:
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
    """
    if source_file is None:
        source_file = filepath

    try:
        doc = fitz.open(filepath)

        if not doc.is_pdf:
            raise ValueError("PDF 형식이 아닌 파일입니다.")
        if len(doc) == 0:
            raise ValueError("페이지가 없는 빈 PDF입니다.")

        # EasyOCR Reader 초기화 (한국어+영어, CPU 전용)
        # 모델 파일을 메모리에 올리는 작업이라 처음 한 번만 오래 걸린다.
        reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)

        pages = []
        for page_num, page in enumerate(doc, start=1):

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
                flagged = bool(ocr_confidence < 0.7)

                # 블록들을 줄바꿈으로 이어붙여 날것 텍스트로 만든다.
                # 마크다운 변환 X — 그건 ② 송승현 담당
                page_text = "\n".join(text for _, text, _ in raw)

            pages.append({
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
            "pages": pages,
        }

    except Exception as e:
        return {
            "source_file": source_file,
            "status": "failed",
            "error": str(e),
            "pages": [],
        }


def parse_pdf(filepath: str, source_file: str = None) -> dict:
    """
    최종 입구 함수: PDF 경로 하나를 받아 CLAUDE.md 4번 형식의 결과 객체를 반환한다.

    ── 내부 처리 흐름 ───────────────────────────────────────────────────
    이 함수 하나가 2~5단계를 순서대로 처리한다.

    1. PDF 파일을 연다.
       → 열 수 없으면 status="failed" 객체를 바로 반환 (프로그램 멈추지 않음)

    2. 페이지를 하나씩 돌면서 판별 → 추출을 이어서 처리한다:

       get_text()로 텍스트 레이어를 확인 (2단계)
         │
         ├── 글자가 충분히 있음 → "텍스트형"
         │     텍스트를 그대로 꺼낸다 (3단계)
         │     method="text",  ocr_confidence=null,  flagged=false
         │
         └── 글자가 없거나 너무 짧음 → "스캔형"
               페이지를 이미지로 변환 → EasyOCR 실행 (4단계)
               글자 수 가중 평균으로 신뢰도 계산
               0.7 미만이면 flagged=true (5단계)
               method="ocr"

    3. 모든 페이지 결과를 pages 리스트에 모아 반환 (6단계)

    ⭐ EasyOCR Reader는 스캔 페이지가 처음 나올 때 딱 한 번만 켜진다.
       텍스트형 PDF만 처리할 때는 EasyOCR이 아예 로드되지 않아 속도가 빠르다.
    ─────────────────────────────────────────────────────────────────────
    """
    if source_file is None:
        source_file = filepath

    try:
        doc = fitz.open(filepath)

        if not doc.is_pdf:
            raise ValueError("PDF 형식이 아닌 파일입니다.")
        if len(doc) == 0:
            raise ValueError("페이지가 없는 빈 PDF입니다.")

        reader = None  # 스캔 페이지가 나올 때 처음 한 번만 초기화

        pages = []
        for page_num, page in enumerate(doc, start=1):

            # ── 판별: 텍스트 레이어 유무 확인 (2단계) ───────────────────
            # get_text() 결과를 변수에 담아 두면, 텍스트형일 때 다시 호출 안 해도 된다.
            page_text_raw = page.get_text()
            is_text_page = len(page_text_raw.strip()) >= TEXT_THRESHOLD

            if is_text_page:
                # ── 텍스트형 경로 (3단계) ─────────────────────────────
                pages.append({
                    "page": page_num,
                    "text": page_text_raw,  # 날것 그대로 (마크다운 변환 X)
                    "method": "text",
                    "ocr_confidence": None,
                    "flagged": False,
                })

            else:
                # ── 스캔형 경로 (4단계 + 5단계) ──────────────────────
                # Reader 초기화: 스캔 페이지가 처음 등장하는 순간 한 번만 실행
                if reader is None:
                    reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)

                # 페이지 → numpy 이미지 배열 (2배 해상도)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                if pix.n == 4:      # RGBA이면 알파 채널 제거
                    img = img[:, :, :3]

                raw = reader.readtext(img)

                # 글자 수 가중 평균 계산 (4단계)
                total_chars = sum(len(txt) for _, txt, _ in raw)

                if total_chars == 0:
                    # 빈 페이지(백지·도면 등): 글자를 전혀 못 읽음
                    ocr_confidence = 0.0
                    flagged = True
                    ocr_text = ""
                else:
                    weighted_sum = sum(len(txt) * conf for _, txt, conf in raw)
                    ocr_confidence = float(round(weighted_sum / total_chars, 4))
                    flagged = bool(ocr_confidence < 0.7)  # 5단계 기준
                    ocr_text = "\n".join(txt for _, txt, _ in raw)

                pages.append({
                    "page": page_num,
                    "text": ocr_text,
                    "method": "ocr",
                    "ocr_confidence": ocr_confidence,
                    "flagged": flagged,
                })

        doc.close()
        return {
            "source_file": source_file,
            "status": "success",
            "error": None,
            "pages": pages,
        }

    except Exception as e:
        err_msg = str(e)
        # fitz가 파일을 열지 못할 때 나오는 영문 메시지를 한국어로 교체
        if "Failed to open" in err_msg or "cannot open" in err_msg.lower():
            err_msg = "PDF를 열 수 없음 (파일이 손상되었거나 형식이 올바르지 않습니다)"
        return {
            "source_file": source_file,
            "status": "failed",
            "error": err_msg,
            "pages": [],
        }

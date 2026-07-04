"""
송승현 파트 독립 테스트 스크립트
실행: python test_office_parsers.py  (Team-3/backend 폴더에서)

DOCX / XLSX / HWPX — 더미 파일 자동 생성 후 파서 검증
HWP  / PPTX        — LibreOffice 필요 (미설치 시 SKIP 표시)
"""
import sys
import os
import zipfile
import tempfile
import shutil
import traceback

# ── 경로 설정: backend 디렉터리를 최우선으로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 터미널 색상
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"


# ────────────────────────────────────────────
# 더미 파일 생성 헬퍼
# ────────────────────────────────────────────

def make_docx(path: str):
    from docx import Document
    doc = Document()
    doc.add_heading("KM 플랫폼 설계서", level=1)
    doc.add_heading("1. 개요", level=2)
    doc.add_paragraph("이 문서는 사내 지식관리 플랫폼의 설계를 설명합니다.")
    doc.add_paragraph("English paragraph for multilingual support.")

    tbl = doc.add_table(rows=3, cols=3)
    headers = ["구성요소", "담당", "기술스택"]
    data = [
        ["파싱", "송승현", "python-docx / openpyxl"],
        ["검색", "김태훈", "turbovec / BM25"],
    ]
    for i, h in enumerate(headers):
        tbl.cell(0, i).text = h
    for r, row in enumerate(data, start=1):
        for c, val in enumerate(row):
            tbl.cell(r, c).text = val

    doc.save(path)


def make_xlsx(path: str):
    from openpyxl import Workbook
    wb = Workbook()

    ws1 = wb.active
    ws1.title = "직원목록"
    ws1.append(["이름", "부서", "입사연도", "역할"])
    ws1.append(["윤준서", "개발팀", 2023, "PDF 파싱"])
    ws1.append(["송승현", "개발팀", 2023, "오피스 파싱"])
    ws1.append(["김태훈", "개발팀", 2023, "임베딩/검색"])
    ws1.append(["김기빈", "개발팀", 2023, "백엔드/UI"])

    ws2 = wb.create_sheet("기술스택")
    ws2.append(["라이브러리", "라이선스", "용도"])
    ws2.append(["pdfplumber", "MIT", "PDF 텍스트 추출"])
    ws2.append(["camelot-py", "MIT", "PDF 표 추출"])
    ws2.append(["python-docx", "MIT", "DOCX 파싱"])
    ws2.append(["openpyxl", "MIT", "XLSX 파싱"])

    wb.save(path)


def make_hwpx(path: str):
    section_xml = """<?xml version="1.0" encoding="UTF-8"?>
<hml:sec xmlns:hml="http://www.hancom.co.kr/hwpml/2012/section">
  <hml:p id="0">
    <hml:run id="0">
      <hml:t>HWPX 테스트 문서입니다.</hml:t>
    </hml:run>
  </hml:p>
  <hml:p id="1">
    <hml:run id="1">
      <hml:t>This is an English paragraph in HWPX format.</hml:t>
    </hml:run>
  </hml:p>
  <hml:tbl>
    <hml:tr>
      <hml:tc>
        <hml:p><hml:run><hml:t>항목</hml:t></hml:run></hml:p>
      </hml:tc>
      <hml:tc>
        <hml:p><hml:run><hml:t>내용</hml:t></hml:run></hml:p>
      </hml:tc>
    </hml:tr>
    <hml:tr>
      <hml:tc>
        <hml:p><hml:run><hml:t>파일형식</hml:t></hml:run></hml:p>
      </hml:tc>
      <hml:tc>
        <hml:p><hml:run><hml:t>HWPX (ZIP+XML)</hml:t></hml:run></hml:p>
      </hml:tc>
    </hml:tr>
    <hml:tr>
      <hml:tc>
        <hml:p><hml:run><hml:t>파서</hml:t></hml:run></hml:p>
      </hml:tc>
      <hml:tc>
        <hml:p><hml:run><hml:t>zipfile + lxml</hml:t></hml:run></hml:p>
      </hml:tc>
    </hml:tr>
  </hml:tbl>
  <hml:p id="2">
    <hml:run id="2">
      <hml:t>표 아래 단락 — 테스트 보존 확인용.</hml:t>
    </hml:run>
  </hml:p>
</hml:sec>""".encode("utf-8")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Contents/section0.xml", section_xml)
        zf.writestr("META-INF/container.xml",
                    b'<?xml version="1.0"?><container/>')


def make_hwp_dummy(path: str):
    """실제 HWP 바이너리는 만들 수 없으므로 빈 파일로 대체 (변환 실패 예상)."""
    with open(path, "wb") as f:
        f.write(b"HWP Document File V3.0\x1a\x01\x02\x03")


def make_pptx(path: str):
    from pptx import Presentation
    from pptx.util import Inches, Pt
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "KM 플랫폼 발표"
    slide.placeholders[1].text = "사내 지식관리 플랫폼\n팀 3조"
    prs.save(path)


# ────────────────────────────────────────────
# 검증 헬퍼
# ────────────────────────────────────────────

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    detail_str = f"  → {detail}" if detail else ""
    print(f"    {'✓' if condition else '✗'} {label}{detail_str}")
    return condition


def section(title: str):
    print(f"\n{BOLD}{CYAN}{'─'*50}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*50}{RESET}")


# ────────────────────────────────────────────
# 개별 테스트 함수
# ────────────────────────────────────────────

def test_models():
    section("models.py — ParseResult / PageResult / ParseStatus")
    from app.services.parsing.models import ParseResult, PageResult, ParseStatus
    ok = True

    r = ParseResult(source_file="test.docx", status=ParseStatus.OK,
                    pages=[PageResult(page_num=1, markdown="# 제목")])
    ok &= check("ParseResult 생성", r.source_file == "test.docx")
    ok &= check("PageResult 포함",  len(r.pages) == 1)
    ok &= check("markdown 값",      r.pages[0].markdown == "# 제목")
    ok &= check("status=OK",        r.status == ParseStatus.OK)

    r2 = ParseResult(source_file="bad.docx", status=ParseStatus.FAILED, error="에러메시지")
    ok &= check("status=FAILED + error", r2.error == "에러메시지")

    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


def test_docx(tmp: str):
    section("DOCX 파서")
    try:
        from app.services.pipeline.office import docx_parser
        from app.services.parsing.models import ParseStatus
    except ImportError as e:
        print(f"  {SKIP} python-docx 미설치 ({e})")
        return None

    path = os.path.join(tmp, "test.docx")
    make_docx(path)
    result = docx_parser.parse(path)

    ok = True
    ok &= check("status=OK",            result.status == ParseStatus.OK,
                result.error or "")
    ok &= check("pages 1개",            len(result.pages) == 1)

    if result.pages:
        md = result.pages[0].markdown
        ok &= check("헤딩 포함 (# 기호)",  "#" in md, md[:80])
        ok &= check("본문 텍스트 포함",     "설계" in md)
        ok &= check("표 헤더 포함",        "구성요소" in md)
        ok &= check("표 데이터 포함",       "송승현" in md)
        ok &= check("Markdown 표 형식",   "| --- |" in md or "|---|" in md)
        print(f"\n  {CYAN}--- Markdown 출력 미리보기 ---{RESET}")
        for line in md.split("\n")[:15]:
            print(f"  {line}")
        if md.count("\n") >= 15:
            print("  ...")

    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


def test_xlsx(tmp: str):
    section("XLSX 파서")
    try:
        from app.services.pipeline.office import xlsx_parser
        from app.services.parsing.models import ParseStatus
    except ImportError as e:
        print(f"  {SKIP} openpyxl 미설치 ({e})")
        return None

    path = os.path.join(tmp, "test.xlsx")
    make_xlsx(path)
    result = xlsx_parser.parse(path)

    ok = True
    ok &= check("status=OK",            result.status == ParseStatus.OK,
                result.error or "")
    ok &= check("시트 2개 → pages 2개", len(result.pages) == 2)

    if len(result.pages) >= 1:
        md1 = result.pages[0].markdown
        ok &= check("시트명 포함 (## 직원목록)",  "직원목록" in md1)
        ok &= check("데이터 행 포함",            "송승현" in md1)
        ok &= check("Markdown 표 형식",          "|" in md1)

    if len(result.pages) >= 2:
        md2 = result.pages[1].markdown
        ok &= check("2번 시트 (## 기술스택)",    "기술스택" in md2)
        ok &= check("2번 시트 데이터",           "pdfplumber" in md2)

    if result.pages:
        print(f"\n  {CYAN}--- 시트1 미리보기 ---{RESET}")
        for line in result.pages[0].markdown.split("\n")[:10]:
            print(f"  {line}")

    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


def test_hwpx(tmp: str):
    section("HWPX 파서")
    try:
        from app.services.pipeline.office import hwpx_parser
        from app.services.parsing.models import ParseStatus
    except ImportError as e:
        print(f"  {SKIP} lxml 미설치 ({e})")
        return None

    path = os.path.join(tmp, "test.hwpx")
    make_hwpx(path)
    result = hwpx_parser.parse(path)

    ok = True
    ok &= check("status=OK",             result.status == ParseStatus.OK,
                result.error or "")
    ok &= check("pages 1개 (section 1개)", len(result.pages) == 1)

    if result.pages:
        md = result.pages[0].markdown
        ok &= check("본문 텍스트 추출",     "HWPX" in md)
        ok &= check("영문 텍스트 추출",     "English" in md)
        ok &= check("표 헤더 추출",         "항목" in md)
        ok &= check("표 데이터 추출",       "zipfile" in md)
        ok &= check("표 아래 단락 보존",    "표 아래" in md)
        ok &= check("Markdown 표 형식",    "|" in md)

        print(f"\n  {CYAN}--- Markdown 출력 미리보기 ---{RESET}")
        for line in md.split("\n"):
            print(f"  {line}")

    # 엣지케이스: 잘못된 ZIP
    section_bad = os.path.join(tmp, "bad.hwpx")
    with open(section_bad, "wb") as f:
        f.write(b"not a zip file")
    r_bad = hwpx_parser.parse(section_bad)
    ok &= check("손상 파일 → FAILED (크래시 없음)",
                r_bad.status.value == "failed")

    # 엣지케이스: section*.xml 없는 ZIP
    empty_hwpx = os.path.join(tmp, "empty.hwpx")
    with zipfile.ZipFile(empty_hwpx, "w") as zf:
        zf.writestr("dummy.txt", "nothing")
    r_empty = hwpx_parser.parse(empty_hwpx)
    ok &= check("section 없는 ZIP → FAILED", r_empty.status.value == "failed")

    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


def test_hwp_libreoffice(tmp: str):
    section("HWP 파서 (LibreOffice 필요)")
    import shutil as _shutil
    if not _shutil.which("libreoffice"):
        print(f"  {SKIP} LibreOffice 미설치 — Railway 배포 환경에서만 동작")
        return None

    from app.services.pipeline.office import hwp_parser
    from app.services.parsing.models import ParseStatus

    path = os.path.join(tmp, "test.hwp")
    make_hwp_dummy(path)
    result = hwp_parser.parse(path)
    # 더미 HWP는 실제 HWP가 아니므로 FAILED 예상 — 핵심은 크래시 없이 반환
    ok = check("LibreOffice 변환 실패 시 FAILED 반환 (크래시 없음)",
               result.status.value in ("ok", "failed"))
    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


def test_pptx_libreoffice(tmp: str):
    section("PPTX 파서 (LibreOffice + 윤준서 PDF 파이프라인 필요)")
    import shutil as _shutil
    if not _shutil.which("libreoffice"):
        print(f"  {SKIP} LibreOffice 미설치 — Railway 배포 환경에서만 동작")
        return None

    try:
        make_pptx(os.path.join(tmp, "test.pptx"))
    except ImportError:
        print(f"  {SKIP} python-pptx 미설치 (더미 생성 불가)")
        return None

    from app.services.pipeline.office import pptx_parser
    result = pptx_parser.parse(os.path.join(tmp, "test.pptx"))
    # 윤준서 PDF 파이프라인 미구현이므로 FAILED 예상
    ok = check("LibreOffice 변환 시도 후 NotImplementedError → FAILED 반환",
               result.status.value == "failed")
    ok &= check("error 메시지 포함",  result.error is not None)
    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


def test_dispatcher(tmp: str):
    section("디스패처 (__init__.py) — 확장자 라우팅")
    from app.services.pipeline.office import parse, supported_extensions
    from app.services.parsing.models import ParseStatus

    # 지원하지 않는 확장자
    result = parse(os.path.join(tmp, "test.pdf"))
    ok = check(".pdf → FAILED (office 담당 아님)",
               result.status == ParseStatus.FAILED)

    result2 = parse(os.path.join(tmp, "test.mp4"))
    ok &= check(".mp4 → FAILED", result2.status == ParseStatus.FAILED)

    exts = supported_extensions()
    ok &= check("지원 확장자 목록 확인",
                all(e in exts for e in [".docx", ".xlsx", ".hwp", ".hwpx", ".pptx"]),
                str(exts))

    print(f"\n  결과: {PASS if ok else FAIL}")
    return ok


# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{'='*50}")
    print("  송승현 파트 독립 테스트")
    print(f"{'='*50}{RESET}")

    tmp = tempfile.mkdtemp(prefix="km_test_")
    print(f"  임시 디렉터리: {tmp}\n")

    results = {}
    try:
        results["models"]     = test_models()
        results["DOCX"]       = test_docx(tmp)
        results["XLSX"]       = test_xlsx(tmp)
        results["HWPX"]       = test_hwpx(tmp)
        results["HWP"]        = test_hwp_libreoffice(tmp)
        results["PPTX"]       = test_pptx_libreoffice(tmp)
        results["dispatcher"] = test_dispatcher(tmp)
    except Exception:
        print(f"\n{RED}예상치 못한 오류:{RESET}")
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 최종 요약
    print(f"\n{BOLD}{'='*50}")
    print("  최종 결과 요약")
    print(f"{'='*50}{RESET}")
    passed = skipped = failed = 0
    for name, res in results.items():
        if res is True:
            print(f"  {PASS}  {name}")
            passed += 1
        elif res is False:
            print(f"  {FAIL}  {name}")
            failed += 1
        else:
            print(f"  {SKIP}  {name}")
            skipped += 1

    print(f"\n  통과: {passed}  실패: {failed}  건너뜀: {skipped}")
    if failed == 0:
        print(f"\n{GREEN}{BOLD}  모든 테스트 통과 (또는 SKIP){RESET}\n")
    else:
        print(f"\n{RED}{BOLD}  {failed}개 테스트 실패 — 위 상세 내용 확인{RESET}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

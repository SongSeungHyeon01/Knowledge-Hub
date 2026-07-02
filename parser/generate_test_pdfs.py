"""
더미 테스트 PDF 생성 스크립트 (파서 검증용 — 일회성 로컬 도구)

⚠️ 이 스크립트는 파이프라인 코드가 아니다. 파서를 검증할 때 쓰는 테스트 자산을
   만들기 위한 보조 도구다. 여기서 쓰는 reportlab/Pillow는 파서 실행에는 필요 없으므로
   parser/requirements.txt에는 넣지 않는다. 이 스크립트를 돌릴 때만 따로 설치한다:
       pip install reportlab Pillow

왜 이 스크립트가 필요한가:
   테스트용 더미 PDF는 용량 문제로 .gitignore(*.pdf)에 의해 레포에 올라가지 않는다.
   따라서 파일 자체는 공유되지 않고, 이 "레시피"만 공유된다. 다음 사람은 이 스크립트를
   실행해 동일한 검증용 더미 7종을 재현한다. (자세한 사양은 같은 폴더의 TEST_FIXTURES.md 참고)

사용법 (레포 루트나 어디서든):
   python parser/generate_test_pdfs.py
   -> 레포 루트의 dummy_pdfs/ 폴더에 7개 PDF가 생성된다.

생성되는 7종:
   text, scan, broken, multi(표 포함), mixed, scan_table(스캔 표), scan_lowconf(저품질 스캔)
"""
import os

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

# ── 재현성 설정 ──────────────────────────────────────────────────────────
# scan_lowconf는 노이즈에 난수를 쓰므로 시드를 고정해 매번 같은 결과가 나오게 한다.
np.random.seed(20260703)

# 한글 지원 폰트 (Helvetica는 한글 미지원 -> 전부 깨짐. 반드시 한글 폰트를 써야 한다.)
# canvas용 CID 폰트(reportlab 내장), 이미지 렌더링용 트루타입 폰트(Windows 기본 맑은 고딕).
pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
KFONT = "HYSMyeongJo-Medium"
KOREAN_TTF = r"C:\Windows\Fonts\malgun.ttf"  # 다른 OS면 한글 트루타입 폰트 경로로 교체

# 출력 폴더: 이 스크립트(parser/)의 부모(레포 루트) 아래 dummy_pdfs/
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dummy_pdfs")
OUT = os.path.abspath(OUT)
os.makedirs(OUT, exist_ok=True)


def render_text_image(lines, size=(1600, 2200), font_size=40, line_gap=70):
    """텍스트를 이미지로 '그림처럼' 렌더링 (스캔본 흉내 — PDF에 텍스트 레이어가 안 생긴다)"""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(KOREAN_TTF, font_size)
    y = 100
    for line in lines:
        draw.text((100, y), line, fill="black", font=font)
        y += line_gap
    return img


def _put_image_page(c, img, path):
    tmp = path + ".tmp.png"
    img.save(tmp)
    c.drawImage(tmp, 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    os.remove(tmp)


# ── 1. text: 텍스트 레이어 있는 일반 PDF (1페이지) ────────────────────────
def make_text(path):
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont(KFONT, 12)
    for i, line in enumerate(["text 테스트 PDF", "일반 텍스트 레이어가 있는 문서입니다.", "두 번째 줄입니다."]):
        c.drawString(50, 800 - i * 20, line)
    c.showPage()
    c.save()


# ── 2. scan: 텍스트 레이어 없는 이미지 PDF (1페이지) ──────────────────────
def make_scan(path):
    c = canvas.Canvas(path, pagesize=A4)
    img = render_text_image(["scan 테스트 PDF", "이 페이지는 이미지로만 구성됩니다", "텍스트 레이어가 전혀 없어야 합니다"])
    _put_image_page(c, img, path)
    c.save()


# ── 3. broken: 실제로 열리지 않는 손상 파일 ───────────────────────────────
def make_broken(path):
    tmp = path + ".valid.tmp"
    c = canvas.Canvas(tmp, pagesize=A4)
    c.setFont(KFONT, 12)
    c.drawString(50, 800, "이 내용은 broken 테스트용 원본입니다.")
    c.showPage()
    c.save()
    with open(tmp, "rb") as f:
        data = f.read()
    with open(path, "wb") as f:
        f.write(data[: int(len(data) * 0.4)])  # 앞 40%만 남기고 잘라 EOF 등 소실 -> 열기 실패
    os.remove(tmp)


# ── 4. multi: 4페이지, 2페이지에 격자선 있는 표 (camelot lattice 검증용) ──
def make_multi(path):
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont(KFONT, 12)
    c.drawString(50, 800, "multi 테스트 - 1페이지 - 일반 텍스트")
    c.drawString(50, 780, "여러 줄의 본문입니다.")
    c.showPage()
    # 2페이지: 표 + 표 위/아래 텍스트
    data = [
        ["이름", "부서", "직급", "입사일"],
        ["김철수", "개발팀", "선임", "2023-01-15"],
        ["이영희", "기획팀", "대리", "2024-03-02"],
        ["박민수", "디자인팀", "사원", "2025-07-20"],
    ]
    table = Table(data, colWidths=[100, 100, 80, 100])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
        ("FONTNAME", (0, 0), (-1, -1), KFONT),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
    ]))
    table.wrapOn(c, A4[0], A4[1])
    table.drawOn(c, 60, 550)
    c.setFont(KFONT, 12)
    c.drawString(50, 800, "표 페이지: 표 위 텍스트")
    c.drawString(50, 500, "표 아래 텍스트 (표 밖 영역)")
    c.showPage()
    for pg in [3, 4]:
        c.setFont(KFONT, 12)
        c.drawString(50, 800, f"multi 테스트 - {pg}페이지 - 일반 텍스트")
        c.showPage()
    c.save()


# ── 5. mixed: 1페이지 텍스트형 + 2페이지 스캔형 ───────────────────────────
def make_mixed(path):
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont(KFONT, 12)
    c.drawString(50, 800, "mixed 테스트 - 1페이지 - 텍스트형 페이지입니다.")
    c.drawString(50, 780, "이 페이지는 텍스트 레이어가 있습니다.")
    c.showPage()
    img = render_text_image(["mixed 테스트 - 2페이지", "이 페이지는 이미지로만 되어있습니다", "텍스트 레이어가 없어야 합니다"])
    _put_image_page(c, img, path)
    c.save()


# ── 6. scan_table: 스캔 이미지 안에 격자선 있는 표 (img2table 검증용) ─────
def make_scan_table(path):
    img = Image.new("RGB", (1600, 2200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(KOREAN_TTF, 36)
    draw.text((100, 80), "scan_table 테스트 - 스캔된 표 이미지", fill="black", font=font)
    data = [
        ["이름", "부서", "직급", "입사일"],
        ["최유진", "영업팀", "과장", "2022-05-11"],
        ["정하늘", "재무팀", "대리", "2023-09-01"],
        ["강민재", "인사팀", "사원", "2024-11-20"],
    ]
    x0, y0, col_w, row_h, n_rows, n_cols = 100, 250, 300, 90, 4, 4
    for r in range(n_rows + 1):
        draw.line([(x0, y0 + r * row_h), (x0 + n_cols * col_w, y0 + r * row_h)], fill="black", width=3)
    for cc in range(n_cols + 1):
        draw.line([(x0 + cc * col_w, y0), (x0 + cc * col_w, y0 + n_rows * row_h)], fill="black", width=3)
    for r in range(n_rows):
        for cc in range(n_cols):
            draw.text((x0 + cc * col_w + 20, y0 + r * row_h + 25), data[r][cc], fill="black", font=font)
    draw.text((100, y0 + (n_rows + 1) * row_h + 40), "표 아래 텍스트 (스캔 이미지 안의 표 밖 영역)", fill="black", font=font)
    c = canvas.Canvas(path, pagesize=A4)
    _put_image_page(c, img, path)
    c.save()


# ── 7. scan_lowconf: 흐림+노이즈로 저품질화한 스캔 (0.7 문턱 flagged 검증) ─
def make_scan_lowconf(path):
    img = Image.new("RGB", (1600, 2200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(KOREAN_TTF, 22)
    y = 100
    for line in ["scan_lowconf 테스트", "저품질 스캔 흉내", "글자가 흐릿하게 보여야 합니다"]:
        draw.text((100, y), line, fill="black", font=font)
        y += 32
    img = img.filter(ImageFilter.GaussianBlur(1.6))
    arr = np.array(img).astype(np.int16)
    noise = np.random.randint(-45, 45, arr.shape, dtype=np.int16)
    arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
    c = canvas.Canvas(path, pagesize=A4)
    _put_image_page(c, Image.fromarray(arr), path)
    c.save()


if __name__ == "__main__":
    make_text(os.path.join(OUT, "text.pdf"))
    make_scan(os.path.join(OUT, "scan.pdf"))
    make_broken(os.path.join(OUT, "broken.pdf"))
    make_multi(os.path.join(OUT, "multi.pdf"))
    make_mixed(os.path.join(OUT, "mixed.pdf"))
    make_scan_table(os.path.join(OUT, "scan_table.pdf"))
    make_scan_lowconf(os.path.join(OUT, "scan_lowconf.pdf"))
    print("생성 완료 ->", OUT)
    print(sorted(os.listdir(OUT)))

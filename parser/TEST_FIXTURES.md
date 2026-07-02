# 파서 검증용 테스트 더미(7종) 레시피

> 작성일: 2026-07-03 · 담당: 윤준서 ① (PDF 파싱)
> 목적: PyMuPDF → pdfplumber+camelot+img2table 교체 작업의 검증에 쓴 더미 PDF 7종을
> 다음 사람이 그대로 재현할 수 있도록 사양과 생성 방법을 기록한다.

## 왜 레시피만 있고 파일은 없나
테스트용 PDF는 용량 문제로 `.gitignore`(`*.pdf`, `dummy_pdfs/`)에 걸려 레포에 올라가지 않는다.
따라서 **더미 파일 자체는 공유되지 않고, 만드는 법(이 문서 + 생성 스크립트)만 공유된다.**

## 재현 방법
```bash
# 파서 실행에는 필요 없는 테스트 전용 도구를 따로 설치 (requirements.txt에는 안 넣음)
pip install reportlab Pillow

# 레포 루트에서 실행하면 dummy_pdfs/ 폴더에 7개 PDF가 생성된다
python parser/generate_test_pdfs.py
```
- 생성 스크립트: [`parser/generate_test_pdfs.py`](generate_test_pdfs.py)
- 한글 폰트: Windows 기본 맑은 고딕(`C:\Windows\Fonts\malgun.ttf`)을 쓴다. 다른 OS면 스크립트
  상단 `KOREAN_TTF` 경로를 그 OS의 한글 트루타입 폰트로 바꿔야 한다.
  (⚠️ 한글 폰트를 안 쓰면 reportlab 기본 Helvetica가 한글을 전부 깨뜨린다 — 실제로 처음에
  이 문제로 검증이 무의미해질 뻔했다.)
- `scan_lowconf`는 난수 노이즈를 쓰므로 스크립트에 시드(`np.random.seed(20260703)`)를 고정해
  매번 동일하게 재현되게 했다.

## 7종 사양

| # | 파일 | 페이지 | 특성 | 이 더미로 검증하는 것 |
|---|---|---|---|---|
| 1 | `text.pdf` | 1 | 텍스트 레이어 있는 일반 PDF | classify=text, pdfplumber 텍스트 추출 |
| 2 | `scan.pdf` | 1 | 텍스트 레이어 없는 이미지 PDF | classify=scan, EasyOCR 경로 |
| 3 | `broken.pdf` | - | 정상 PDF를 앞 40%만 남기고 잘라 손상 | status=failed 처리, 파이프라인이 안 죽는지 |
| 4 | `multi.pdf` | 4 | 다중 페이지, **2페이지에 격자선 표** | 페이지 번호 정합성 + camelot(lattice) 표 통합 |
| 5 | `mixed.pdf` | 2 | 1p=텍스트형, 2p=스캔형 | 페이지별 함수 라우팅(text/ocr 분기) |
| 6 | `scan_table.pdf` | 1 | **스캔 이미지 안에 격자선 표** | img2table 표 인식 + reader 재사용 |
| 7 | `scan_lowconf.pdf` | 1 | 흐림+노이즈로 저품질화한 스캔 | OCR 신뢰도 문턱값(flagged=True) 경로 |

> 1~5는 설계서 4장의 "더미 5종"이다. 6~7은 교체 작업 중 표 인식·flagged 경로를 실제로
> 검증하려고 추가했다(원 설계서에는 없던 보조 자산).

## 표/스캔 더미의 기대 동작 (검증 기준)
- **multi 2페이지**: "표 위 텍스트"·"표 아래 텍스트"는 일반 텍스트로 남고, 표 내용(김철수 등)은
  마크다운 파이프 표로 **딱 한 번만** 나온다 (좌표 기반 중복 제거).
- **scan_table**: 표 밖 텍스트 보존 + 표는 마크다운 파이프 표. (최유진 등 1회만)
- **scan / scan_lowconf**: 렌더링 엔진을 pdfplumber(pypdfium2)로 바꾼 뒤 OCR 신뢰도 baseline이
  낮아졌다. 현재 문턱값(`OCR_CONFIDENCE_THRESHOLD = 0.7`) 기준으로 scan은 경계에 가깝고
  (약 0.66 → flagged=True로 넘어감), scan_lowconf는 확실히 flagged된다. 이 문턱값은 실제
  문서 투입 후 재조정 대상이다(파서 코드 상단 주석 참고).

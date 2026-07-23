# ══════════════════════════════════════════════════════════════════════════
# km-platform 배포 이미지 (Railway Pro)
#
# 2단계(멀티스테이지) 빌드:
#   1단계: React 프론트를 빌드해 정적 파일(dist/)을 만든다
#   2단계: Python 백엔드에 그 정적 파일을 넣어 "서비스 1개"로 통합 서빙한다
#
# ⚠️ ghostscript는 넣지 않는다 — AGPL(07/02 회의 전면 배제 확정).
#    camelot 2.0은 pdfium 백엔드라 ghostscript가 필요 없다 (검증됨).
# ══════════════════════════════════════════════════════════════════════════

# ── 1단계: 프론트 빌드 ───────────────────────────────────────────────────────
FROM node:20-slim AS frontend-build
WORKDIR /front

# 의존성 먼저 복사·설치 (소스만 바뀌면 이 레이어는 캐시 재사용 → 빌드 빨라짐)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# VITE_API_URL을 빈 문자열로 굽는다 → 프론트의 `${API}/upload`가 `/upload`
# 상대 경로가 되어, 같은 서버(통합 서빙)의 백엔드 API를 그대로 호출한다.
ENV VITE_API_URL=""
RUN npm run build


# ── 2단계: 백엔드 + 정적 서빙 ────────────────────────────────────────────────
# python:3.11-slim — 설계도 확정 버전 (camelot·img2table 요구 ≥3.10 호환)
FROM python:3.11-slim

# 시스템 패키지:
#   libreoffice-writer  → HWP→DOCX 변환 (② hwp_parser)
#   libreoffice-impress → PPT/PPTX→PDF 변환 (② pptx_parser)
#   fonts-nanum         → 한글 폰트 (없으면 LibreOffice 변환 결과 한글이 깨짐)
#   libgl1, libglib2.0-0 → img2table==2.0.0이 opencv-contrib-python>=4(GUI 빌드)를
#                          하드 의존성으로 강제해서 생기는 요구사항. camelot·easyocr는
#                          opencv-python-headless라 문제없지만, img2table 쪽 opencv가
#                          설치되면 libGL.so.1 없이는 import 시점에 죽는다
#                          (실제 배포 오류 원인: `ImportError: libGL.so.1: cannot open
#                          shared object file` — core/parser.py의 `import camelot`에서 발생)
#   postgresql-client   → pg_dump 바이너리 (2026-07-23 자동 DB 백업 기능이 사용)
#   locales             → UTF-8 로케일 생성용. 베이스 이미지엔 로케일이 전혀 없어 기본이
#                          C/POSIX인데, 이 상태로 LibreOffice headless가 한글 등 비ASCII
#                          파일명이 있는 문서를 변환하면 soffice는 성공(exit 0)했다고
#                          보고하면서도 실제로는 예상한 이름의 결과 파일을 안 만드는 경우가
#                          있다(실제 배포 오류: '이력서 양식 한글 원본.hwp' 업로드 시
#                          "변환 결과 파일을 찾을 수 없습니다" 실패 — 2026-07-24 확인).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-impress \
        fonts-nanum \
        libgl1 \
        libglib2.0-0 \
        postgresql-client \
        locales \
    && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

WORKDIR /app

# 파이썬 의존성 (torch는 반드시 CPU 버전 — extra-index-url 필수)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu

# 백엔드 소스 복사 (.dockerignore가 venv·data·km.db 등을 걸러준다)
COPY backend/ .

# 프론트 빌드 산출물 → backend/static/ (main.py 맨 끝의 StaticFiles가 서빙)
COPY --from=frontend-build /front/dist ./static

# ── 환경변수 ────────────────────────────────────────────────────────────────
# DATA_DIR             업로드 원본·파싱 JSON 저장 위치 → Railway Volume(/data)
# EASYOCR_MODULE_PATH  ① core/parser.py의 EasyOCR 모델 저장 루트
#                      (실제 모델은 /data/easyocr/model 에 저장됨)
#                      core/parser.py 코드는 저장 경로를 지정하지 않지만, EasyOCR
#                      라이브러리(config.py)가 이 환경변수를 자동으로 읽어 적용한다
# EASYOCR_MODEL_DIR    ② _image_ocr.py의 모델 폴더 — ①과 같은 폴더를 가리키게
#                      맞춰서 모델(~500MB) 이중 다운로드를 막는다
# HF_HOME              sentence-transformers(MiniLM ~470MB) 캐시 → Volume
# 첫 기동 때만 모델을 내려받고, 이후엔 Volume 캐시를 재사용한다 (설계도 확정)
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    EASYOCR_MODULE_PATH=/data/easyocr \
    EASYOCR_MODEL_DIR=/data/easyocr/model \
    HF_HOME=/data/hf_cache

EXPOSE 8000

# Railway가 PORT 환경변수를 주입한다 — 없으면 8000 (shell 형식이라 ${...} 확장됨)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

# ══════════════════════════════════════════════════════════════════════════
# km-platform 배포 이미지 (Railway Pro)
#
# 2단계(멀티스테이지) 빌드:
#   1단계: React 프론트를 빌드해 정적 파일(dist/)을 만든다
#   2단계: Python 백엔드에 그 정적 파일을 넣어 "서비스 1개"로 통합 서빙한다
#
# [2026-07-28] 문서 파서를 Docling으로 통합하면서 LibreOffice·H2Orestart(HWP
# 필터)·JRE·img2table용 libGL 의존성을 전부 제거했다 — Docling은 PDF·DOCX·
# PPTX·XLSX를 외부 시스템 바이너리 없이 자체 처리한다. HWP/HWPX는 Docling이
# 지원하지 않아 이 교체와 함께 지원을 포기했다(별도 결정, 업로드 단계에서 거부).
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
FROM python:3.11-slim

# 시스템 패키지: postgresql-client(pg_dump — 2026-07-23 자동 DB 백업 기능이 사용) 하나뿐이다.
# LibreOffice·H2Orestart·JRE·libGL·로케일 생성은 전부 [2026-07-28] Docling 통합과
# HWP 지원 포기로 필요 없어져 제거했다(자세한 이유는 파일 맨 위 주석).
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

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
# DATA_DIR              업로드 원본·파싱 JSON 저장 위치 → Railway Volume(/data)
# EASYOCR_MODULE_PATH   Docling의 OCR 백엔드(EasyOCR)가 쓰는 모델 저장 루트 —
#                       easyocr 라이브러리(config.py)가 이 환경변수를 자동으로 읽는다
# DOCLING_ARTIFACTS_PATH Docling 자체 모델(레이아웃·TableFormer 등) 캐시 위치
# HF_HOME               sentence-transformers(MiniLM ~470MB) 캐시 → Volume
# 첫 기동 때만 모델을 내려받고, 이후엔 Volume 캐시를 재사용한다 (설계도 확정)
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    EASYOCR_MODULE_PATH=/data/easyocr \
    DOCLING_ARTIFACTS_PATH=/data/docling_models \
    HF_HOME=/data/hf_cache

EXPOSE 8000

# Railway가 PORT 환경변수를 주입한다 — 없으면 8000 (shell 형식이라 ${...} 확장됨)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

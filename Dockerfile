# ══════════════════════════════════════════════════════════════════════════
# Knowledge Hub 배포 이미지 (Railway Pro)
#
# 2단계(멀티스테이지) 빌드:
#   1단계: React 프론트를 빌드해 정적 파일(dist/)을 만든다
#   2단계: Python 백엔드에 그 정적 파일을 넣어 "서비스 1개"로 통합 서빙한다
#
# [2026-07-28] 문서 파서를 Docling으로 통합하면서 LibreOffice·H2Orestart(HWP
# 필터)·JRE·img2table용 libGL 의존성을 전부 제거했다 — Docling은 PDF·DOCX·
# PPTX·XLSX를 외부 시스템 바이너리 없이 자체 처리한다. HWP/HWPX는 Docling이
# 지원하지 않아 한때 지원을 포기했다가, [2026-10-24] 한컴 공개 명세 기반 자체 파서
# (backend/core/hwp_parser.py, 파이썬만 사용)로 다시 지원한다 — 시스템 패키지 추가 없음.
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

# ── 모델을 이미지 안에 미리 받아둔다 (2026-10-25) ───────────────────────────────
# DOCLING_ARTIFACTS_PATH를 주면 Docling은 모델을 내려받지 않고 그 폴더에서만 찾는다(완전 오프라인).
# 예전엔 이 값을 /data(실행 시 볼륨이 덮어쓰는 경로)로 두고 모델을 미리 받지 않아, 배포 환경에서
# 파싱 모델을 못 찾을 수 있었다. 폐쇄망 운영을 전제로 빌드 단계에서 /opt/models에 받아둔다.
#   - Docling 레이아웃·표(TableFormer) 모델
#   - RapidOCR 한국어 인식 모델(PP-OCRv5 korean) + 검출·방향 모델
#   - 임베딩 모델(EMBED_MODEL, 기본 KURE-v1) → HF_HOME
ARG EMBED_MODEL=nlpai-lab/KURE-v1
ARG EMBED_DIM=1024
ENV DOCLING_ARTIFACTS_PATH=/opt/models/docling \
    HF_HOME=/opt/models/hf \
    EMBED_MODEL=${EMBED_MODEL} \
    EMBED_DIM=${EMBED_DIM}
RUN docling-tools models download layout tableformer rapidocr \
        --rapidocr-backend-lang onnxruntime:korean -o /opt/models/docling \
    && python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBED_MODEL'])"

# 백엔드 소스 복사 (.dockerignore가 venv·data·km.db 등을 걸러준다)
COPY backend/ .

# 프론트 빌드 산출물 → backend/static/ (main.py 맨 끝의 StaticFiles가 서빙)
COPY --from=frontend-build /front/dist ./static

# ── 환경변수 ────────────────────────────────────────────────────────────────
# DATA_DIR         업로드 원본·파싱 JSON 저장 위치 → 볼륨(/data)
# HF_HUB_OFFLINE   위에서 받아둔 모델만 쓰고 실행 중에는 인터넷에서 받지 않는다(폐쇄망)
# OPENSEARCH_URL   검색 엔진 주소 — 배포 환경에 맞게 반드시 지정(backend/opensearch/ 참고)
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HF_HUB_OFFLINE=1

EXPOSE 8000

# Railway가 PORT 환경변수를 주입한다 — 없으면 8000 (shell 형식이라 ${...} 확장됨)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

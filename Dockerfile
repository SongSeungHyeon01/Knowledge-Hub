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
#   curl                  → H2Orestart 확장 파일(.oxt) 다운로드용 (바로 아래)
#   default-jre-headless  → LibreOffice의 Java 연동이 쓸 JVM 본체
#   libreoffice-java-common → LO ↔ JVM을 실제로 이어주는 브릿지 패키지. JRE만 있고
#                          이 패키지가 없으면 unopkg가 확장을 설치하려 할 때
#                          "[JavaVirtualMachine]: An unexpected error occurred while
#                          searching for a Java"로 실패한다(실제 배포 빌드 실패로 확인,
#                          2026-07-24 — Railway 빌드 로그에서 unopkg 단계가 이 에러로 죽음).
#   clamav, clamav-daemon, clamav-freshclam → 업로드 파일 악성코드 검사(main.py의
#                          _scan_for_malware가 유닉스 소켓으로 통신). freshclam 바이너리는
#                          Debian에서 clamav-daemon의 "Recommends"일 뿐 "Depends"가
#                          아니라서, 이 Dockerfile처럼 --no-install-recommends를 쓰면
#                          명시적으로 같이 넣어주지 않으면 설치가 안 된다 — 시그니처DB가
#                          하나도 없는 채로 clamd가 뜨는 걸 방지하기 위해 명시적으로 추가.
#                          freshclam으로 빌드 시점 시그니처를 미리 받아두고, 컨테이너
#                          시작 시(docker-entrypoint.sh) 한 번 더 갱신을 시도한다 —
#                          시그니처DB만 최소 300MB 정도라 빌드 시간이 꽤 늘어난다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-impress \
        fonts-nanum \
        libgl1 \
        libglib2.0-0 \
        postgresql-client \
        locales \
        curl \
        default-jre-headless \
        libreoffice-java-common \
        clamav \
        clamav-daemon \
        clamav-freshclam \
    && sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen \
    && locale-gen \
    && service clamav-freshclam stop || true \
    && freshclam --quiet || true \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# H2Orestart — LibreOffice에는 HWP(한글) 임포트 필터가 기본 내장돼 있지 않다(이 베이스
# 이미지의 Debian 저장소에도 없음). 이 확장 없이는 유효한 HWP5 파일조차 LibreOffice가
# "source file could not be loaded"로 거부한다 — 로케일·파일명 문제가 아니라 필터
# 자체가 없었던 것 (실제 배포 오류: '이력서 양식 한글 원본.hwp' 업로드 실패로 확인,
# 2026-07-24). HWP5/HWPx 포맷을 지원하며, 옛 HWP 2.0/3.0은 지원하지 않는다.
RUN curl -fsSL -o /tmp/H2Orestart.oxt \
        https://github.com/ebandal/H2Orestart/releases/download/v0.7.13/H2Orestart.oxt \
    && unopkg add --shared --suppress-license /tmp/H2Orestart.oxt \
    && rm -f /tmp/H2Orestart.oxt

WORKDIR /app

# 파이썬 의존성 (torch는 반드시 CPU 버전 — extra-index-url 필수)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu

# 백엔드 소스 복사 (.dockerignore가 venv·data·km.db 등을 걸러준다)
COPY backend/ .

# 프론트 빌드 산출물 → backend/static/ (main.py 맨 끝의 StaticFiles가 서빙)
COPY --from=frontend-build /front/dist ./static

# ClamAV 데몬 설정 + 시작 스크립트 (main.py의 CLAMD_SOCKET 기본값과 경로 일치시킬 것)
COPY clamd.conf ./clamd.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

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

# docker-entrypoint.sh가 clamd(악성코드 검사 데몬)를 먼저 띄우고 소켓이 준비되길
# 잠깐 기다린 뒤 uvicorn을 시작한다 — Railway의 PORT 환경변수는 그 안에서 그대로 읽는다.
CMD ["/docker-entrypoint.sh"]

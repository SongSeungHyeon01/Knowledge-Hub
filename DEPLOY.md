# 배포 안내 (Railway Pro) — 2026-07-04 작성

> 설계도(`0703_시스템_설계도.pdf`) 확정 사항 기준: Railway Pro(RAM 3GB·2vCPU·Volume 10GB),
> PostgreSQL 플러그인, Volume(/data) 모델 캐시. **개발·팀 테스트·시연용** — 실사내 배포는 온프레미스 이전 별도 논의.

## 구성 (서비스 1개, 통합 서빙)

- 루트 `Dockerfile` 하나로 빌드: React 빌드 → FastAPI가 `/`에서 화면까지 서빙
- 프론트는 `VITE_API_URL=""`로 빌드되어 같은 서버의 API를 상대 경로로 호출 (CORS 불필요)

## Railway 설정 절차

1. **서비스 생성**: GitHub 레포 연결 → 루트의 `Dockerfile` 자동 감지
2. **PostgreSQL 플러그인 추가** → `DATABASE_URL`이 서비스에 자동 주입됨
   (백엔드는 `DATABASE_URL` 있으면 PostgreSQL, 없으면 SQLite 폴백 — database.py)
3. **Volume 생성 후 `/data`에 마운트** — 여기에 저장되는 것:
   - 업로드 원본·파싱 JSON (`DATA_DIR=/data`)
   - EasyOCR 모델 캐시 (~500MB, `/data/easyocr/model`)
   - MiniLM 임베딩 모델 캐시 (~470MB, `/data/hf_cache`)
4. 별도 환경변수 설정 **불필요** (Dockerfile ENV에 기본값 포함). 필요 시:
   - `OLLAMA_MODEL` — Phase 2 (Gemma 4 12B) 전용, MVP에선 무시
   - `OLLAMA_BASE_URL` — Ollama 서버 주소. 기본값 `http://localhost:11434`. 사내 폐쇄망에서 Ollama가 다른 호스트에 있을 때만 지정
   - `CORS_ORIGINS` — 통합 서빙이라 기본값으로 충분. 프론트를 분리 배포할 때만 설정

## 첫 기동 시 주의

- **첫 요청 처리 전 모델 다운로드**로 수 분 소요 (EasyOCR + MiniLM ≈ 1GB, 이후 Volume 캐시 재사용)
- 스캔 PDF 첫 OCR 시 EasyOCR 로딩(~1.5GB RAM 순간 사용) — RAM 3GB 내 동작하나
  대형 문서 동시 업로드는 피할 것

## 의도된 제외 사항

- **ghostscript 미포함** — AGPL 전면 배제(07/02 확정). camelot 2.0은 pdfium 백엔드라 불필요.
  (설계도 배포 카드의 "ghostscript" 문구는 이 결정과 모순되어 제외를 택함)
- LLM(Gemma 4 12B)은 Phase 2 — 이 이미지에 Ollama 없음

## 로컬에서 이미지 빌드 시험 (Docker 설치된 PC)

```bash
docker build -t km-platform .
docker run --rm -p 8000:8000 -v km_data:/data km-platform
# 접속: http://localhost:8000  (화면+API 통합)
```

## 검증 상태 (2026-07-04, 노트북 로컬)

- ✅ 프론트 빌드(VITE_API_URL="") + FastAPI 통합 서빙 e2e 검증
- ✅ 업로드→파싱(PDF·DOCX·HWPX·TXT)→청킹→임베딩→하이브리드 검색 전 구간 로컬 검증 (Python 3.14 venv)
- ⚠️ Docker 이미지 빌드 자체는 미검증 (작업 노트북에 Docker 없음) — 첫 배포 시 빌드 로그 확인 필요
- ⚠️ HWP·PPT 변환은 LibreOffice 필요 — 로컬 미설치라 배포 환경에서 첫 검증
- ⚠️ 이미지의 Python은 3.11(설계도 확정) — 로컬 검증은 3.14였음. 의존성 핀은 3.11 호환이나 첫 빌드에서 확인

# 배포 안내

> 2026-07-04 Railway Pro 기준으로 처음 작성했고, 2026-10-25에 검색 엔진(OpenSearch)·OCR(RapidOCR 한국어)·
> 임베딩 모델(KURE-v1) 교체에 맞춰 고쳤다. AX 기간에 쓰던 Railway 배포는 지금 내려가 있고, 다음 서버 구성은
> 아직 정하지 않았다. 아래는 어떤 서버에 올리든 공통으로 필요한 내용이다.

## 운영 구성 (2026-10-25 결정)

**VM 한 대에 `deploy/docker-compose.yml`로 네 서비스를 띄운다.** 밖으로 열리는 포트는 Caddy의 80/443뿐이다.

| 서비스 | 이미지 | 라이선스 |
|---|---|---|
| caddy (HTTPS, Let's Encrypt 자동 발급) | `caddy:2.11.7-alpine` | Apache-2.0 |
| app (화면 + API) | 루트 `Dockerfile` (모델 포함) | 프로젝트 코드 |
| opensearch | `backend/opensearch/Dockerfile` (3.9.0 + nori) | Apache-2.0 |
| postgres | `postgres:17.11-alpine` | PostgreSQL License |

Postgres를 17로 둔 이유: 앱 이미지 안의 `pg_dump`가 17.11이고(자동 백업이 사용), `pg_dump`는 자기보다 높은 버전의 서버를
백업하지 못한다.

```bash
cp deploy/.env.example deploy/.env      # 도메인, DB 비밀번호, 로그인 값 채우기
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

이 PC(Windows, Docker Desktop)에서 `DOMAIN=localhost`로 그대로 띄워 확인했다: HTTP가 HTTPS로 넘어가고, HTTPS로 HWP·HWPX·스캔 PDF를
올려 Postgres에 저장·OpenSearch에 색인된 뒤 유사 검색에 잡혔고, 관리자 백업(`pg_dump`)이 만들어졌다.

### 서버 사양: 4 vCPU 이상, RAM 16GB, 디스크 100GB 이상

문서 23개를 한꺼번에 올리는 동안 2초 간격으로 컨테이너 자원을 기록한 결과(2026-10-25):

| 구성 요소 | 최대 메모리 | 평소 |
|---|---|---|
| app (파싱·임베딩 중) | 4.66 GiB | 약 0.8 GiB |
| opensearch (힙 1GB) | 2.06 GiB | 약 2.0 GiB |
| postgres, caddy | 측정 안 함(수백 MB 수준) | |

합계 최대 약 7GiB에 운영체제 몫이 더해지므로 8GB는 빠듯하고 16GB가 안전하다. 파싱은 CPU 코어를 많이 쓴다(16코어에서 23개 348초).
코어가 적으면 처리만 느려진다. 디스크는 앱 이미지(약 8.6GB, 모델 포함)와 OpenSearch 이미지(약 2.9GB)에 문서·백업이 더해진다.

### 호스팅: Hetzner Cloud CPX42 (싱가포르)

| 후보 | 사양 | 비용 | 판단 |
|---|---|---|---|
| **Hetzner CPX42, 싱가포르** | 8 vCPU(AMD x86), 16GB, 320GB NVMe | €93.99/월 | **선택.** 지금 검증한 x86 이미지를 그대로 쓰고, 한국에서 가깝다 |
| Hetzner CPX42, 유럽 | 같음 | €69.99/월 | 지연이 상관없으면 더 싸다 |
| Hetzner CX43(8 vCPU, 16GB, €16.49) | | | 2026-10-25 기준 "판매 중지" |
| Oracle Cloud Always Free | ARM 2 OCPU, 12GB, 200GB | 무료 | 12GB면 실측 최대치는 들어가지만, ARM용 이미지를 따로 빌드해야 하고(미검증), 7일 동안 CPU·네트워크·메모리 사용률이 20% 미만이면 회수될 수 있다고 공식 문서에 적혀 있다. 7월에 "out of host capacity"로 인스턴스 생성도 실패했다 |
| Railway | 사용량 과금: RAM $10/GB·월, vCPU $20/월 | 평소 사용량 기준 | AX 때 쓰던 곳. 검색 엔진 볼륨 권한 문제가 있었다 |

가격은 각 공식 페이지(2026-10-25 확인)의 부가세 제외 "from" 가격이다. 서버 구매와 도메인 연결은 계정·결제가 필요해 하지 않았다.

### 실제로 올릴 때 순서

1. 서버 생성(Ubuntu), Docker 설치, 저장소 받기
2. 도메인 DNS A 레코드를 서버 IP로 지정
3. `deploy/.env` 작성 (`POSTGRES_PASSWORD`, `SESSION_SECRET`은 긴 무작위 값)
4. Google Cloud Console OAuth 클라이언트에 `https://<도메인>`을 승인된 JavaScript 원본으로 추가
5. `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build`
6. 방화벽은 22(SSH), 80, 443만 연다

사내 폐쇄망으로 옮길 때도 같은 compose를 쓴다. 모델이 이미지 안에 있어서 인터넷이 필요 없고, 이미지는 `docker save` / `docker load`로 반입한다.
이때 Caddy는 사내 인증서를 쓰도록 Caddyfile을 바꾼다.

## 구성 요소 (공통)

- **앱 서비스 1개**: 루트 `Dockerfile` 하나로 빌드한다. React를 빌드한 뒤 FastAPI가 `/`에서 화면까지 함께 서빙한다.
  프론트는 `VITE_API_URL=""`로 빌드되어 같은 서버의 API를 상대 경로로 부른다(CORS 불필요).
- **DB**: `DATABASE_URL`이 있으면 PostgreSQL, 없으면 SQLite(`backend/database.py`).
- **검색 엔진**: OpenSearch 3.9.0 + nori. `backend/opensearch/Dockerfile`로 별도 서비스로 띄운다.
- **볼륨**: `/data`에 업로드 원본·파싱 JSON·백업이 저장된다(`DATA_DIR=/data`).

## 모델은 이미지 안에 들어 있다

루트 `Dockerfile`은 빌드할 때 다음 모델을 `/opt/models`에 미리 받아둔다. 실행 중에는 인터넷에서 모델을 받지
않는다(`HF_HUB_OFFLINE=1`, `DOCLING_ARTIFACTS_PATH=/opt/models/docling`). 폐쇄망 서버에서 그대로 쓰기 위해서다.

| 모델 | 용도 | 라이선스 |
|---|---|---|
| Docling 레이아웃(heron)·표(TableFormer) | 문서 구조·표 인식 | Apache-2.0 / CDLA-Permissive-2.0 |
| RapidOCR PP-OCRv5 한국어 인식 + 검출·방향 모델 | 스캔 페이지 OCR | Apache-2.0 (원본 PaddleOCR) |
| `nlpai-lab/KURE-v1` (빌드 인자 `EMBED_MODEL`) | 임베딩 | MIT |

`/data`는 실행할 때 볼륨이 덮어쓰므로 모델을 그 아래에 두면 안 된다. 예전 설정(`DOCLING_ARTIFACTS_PATH=/data/docling_models`)은
모델을 미리 받지 않은 채 오프라인 모드로 지정돼 있어서 배포 환경에서 파싱 모델을 못 찾을 수 있었다.

임베딩 모델을 바꾸려면 빌드 인자를 함께 준다. 모델과 차원이 다르면 서버가 시작할 때 로그로 알리고 임베딩을 끈다.

```bash
docker build -t knowledge-hub --build-arg EMBED_MODEL=intfloat/multilingual-e5-small --build-arg EMBED_DIM=384 .
```

## 환경변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `OPENSEARCH_URL` | 예 | 검색 엔진 주소(예: `http://opensearch:9200`). 기본값 `http://localhost:9200`. 연결이 안 되면 유사 검색이 0건이 되고 화면에 "검색 엔진 다운"으로 표시된다 |
| `OPENSEARCH_INDEX` | | 인덱스 이름. 기본값 `knowledge_hub_chunks`. 임베딩 모델을 바꿀 때는 새 이름을 준다(벡터 차원이 다르면 같은 인덱스를 못 쓴다) |
| `DATABASE_URL` | | PostgreSQL 주소. 없으면 SQLite |
| `GOOGLE_CLIENT_ID`, `SESSION_SECRET`, `ADMIN_EMAILS` | | 로그인. `GOOGLE_CLIENT_ID`가 없으면 로그인 없이 동작한다 |
| `OCR_LANG` | | RapidOCR 인식 언어(PP-OCR 코드). 기본값 `korean`. 바꾸면 이미지 빌드 때 받는 OCR 모델도 맞춰야 한다 |
| `CORS_ORIGINS` | | 프론트를 따로 배포할 때만 지정 |

## OpenSearch

`backend/opensearch/Dockerfile`은 보안 플러그인(인증·TLS)을 끈 설정이다. 그래서 검색 엔진 포트를 인터넷에 그대로
열면 안 된다. 앱 서비스와 같은 사설 네트워크 안에서만 접근되게 하거나, 보안 플러그인을 켜고 `OPENSEARCH_URL`에
인증 정보를 넣는다. 로컬에서는 `docker-compose.yml`이 `127.0.0.1`에만 포트를 연다.

검색 엔진에 영구 볼륨을 붙이지 못하는 환경이어도 괜찮다. 앱이 10분마다 "DB의 청크 수와 검색 인덱스의 청크 수"를 비교해서
인덱스가 비었으면 DB에 남은 벡터로 다시 채운다(`scripts/search_backfill.py`의 `run_backfill`).

## 첫 기동 시 주의

- 임베딩 모델(KURE-v1, 모델 파일 약 2.3GB)을 서버 시작 직후 백그라운드에서 불러온다. 이 몇십 초 동안 들어온 검색은 모델이
  올라올 때까지 기다린다.
- 문서 상태가 "완료"로 바뀐 뒤 임베딩·색인이 이어서 돈다. 큰 문서는 완료 표시 후 몇십 초 동안 유사 검색에 잡히지 않는다.
- KURE-v1은 MiniLM보다 임베딩이 느리다(노트북 CPU 기준 청크 1,512개에 471초). 대량 업로드 직후에는 색인이 밀릴 수 있다.

## 의도된 제외 사항

- **ghostscript 미포함**: AGPL이라 전면 배제(07/02 확정).
- **LibreOffice·JRE 미포함** (2026-07-28): Docling이 PDF·DOCX·PPTX·XLSX를 외부 프로그램 없이 처리한다. HWP·HWPX는
  2026-10-24부터 자체 파서(`backend/core/hwp_parser.py`)로 처리하므로 역시 외부 프로그램이 필요 없다.
- **Elasticsearch 미사용** (2026-10-24): 배포판 라이선스가 Elastic License 2.0이라 OpenSearch로 교체했다.
- **EasyOCR 미사용** (2026-10-24): 의존성 python-bidi가 LGPL이라 RapidOCR로 교체했다.
- LLM은 쓰지 않는다. 검색 결과는 원본 문서와 페이지로 돌려준다.

## 로컬에서 이미지 빌드 시험

```bash
cd backend/opensearch && docker compose up -d --build && cd ../..
docker build -t knowledge-hub .
docker run --rm -p 8000:8000 -v knowledge_hub_data:/data \
  -e OPENSEARCH_URL=http://host.docker.internal:9200 knowledge-hub
# 접속: http://localhost:8000  (화면+API 통합)
```

## 검증 상태

- 2026-07-04: 프론트 빌드 + FastAPI 통합 서빙, 업로드부터 하이브리드 검색까지 로컬 검증.
- 2026-10-24~25: OpenSearch·RapidOCR·KURE-v1·HWP 파서로 바꾼 뒤 로컬(Windows 11, Python 3.12)에서 측정 문서 23개 업로드와
  질문 47개 유사 검색 검증(`eval/run_eval.py`). 모델을 미리 받은 폴더만으로(오프라인 모드) 스캔 PDF가 파싱되는 것도 확인했다.
- 2026-10-25 이미지 빌드 확인: 루트 `Dockerfile` 빌드 성공(이미지 약 8.6GB, 모델 포함). 컨테이너를 OpenSearch와 같은
  네트워크에 붙여 HWP·HWPX·스캔 PDF를 올리고 유사 검색까지 확인했다. `HF_HUB_OFFLINE=1` 상태에서 임베딩 모델과 OCR 모델을
  이미지 안 경로에서 불러왔다.
- 같은 날 `.dockerignore`를 고쳤다. 예전 설정은 `backend/.env`(세션 서명 키), `backend/km.db`(로컬 문서 데이터),
  `backend/.venv`를 이미지에 넣고 있었다. 지금 이미지의 `/app`에는 소스와 빌드된 화면만 있다.

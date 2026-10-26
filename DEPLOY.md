# 배포 안내

> 2026-07-04 Railway Pro 기준으로 처음 작성했고, 2026-10-25에 검색 엔진(OpenSearch)·OCR(RapidOCR 한국어)·
> 임베딩 모델(KURE-v1) 교체에 맞춰 고쳤다. 2026-10-26부터는 집에 있는 여분 노트북(Ubuntu Server 26.04)에
> 아래 compose 구성으로 올려 운영한다. Oracle 무료 서버는 계획했지만 용량이 안 잡혀 만들지 못했다.

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

### 호스팅: 집 노트북 서버 (2026-10-26, 지금 운영 중)

Oracle 무료 서버가 만들어지지 않아서(아래 표) 집에 있던 여분 노트북을 서버로 쓴다.

- 노트북: RAM 8GB(실사용 7.0GiB), NVMe SSD 238GB, Ubuntu Server 26.04 LTS(화면 없음, LVM 없이 디스크 전체 사용)
- 8GB가 실측 최대치(약 7GiB)에 빠듯해서 스왑을 8GB로 잡았다. 대기 상태에서 3.1GiB를 쓴다.
- 덮개를 닫아도 꺼지지 않게 `logind`를 설정했다. Docker와 컨테이너 4개는 재부팅하면 자동으로 다시 올라온다.
- 외부에는 웹용 80·443만 연다. 공유기 UPnP로 포트를 열고, 공유기 재부팅이나 서버 IP 변경에 대비해 15분마다 다시 건다(systemd 타이머).
- SSH는 집 안에서만 쓰고, 비밀번호 로그인은 막았다(키 전용).
- 도메인은 `<공인 IP를 대시로>.nip.io`. 가정용 공인 IP는 바뀔 수 있어서, 바뀌면 `deploy/.env`의 `DOMAIN`과 Google OAuth 원본을 함께 고쳐야 한다.
- Ubuntu 26.04 설치 프로그램이 와이파이 설정에 넣는 `match:` 항목을 networkd가 지원하지 않아 와이파이가 붙지 않았다. netplan 파일에서 `match:`·`set-name:`을 지우고 `dhcp4: true`를 넣어 해결했다.

#### 검토했던 후보 (2026-10-25)

| 후보 | 사양 | 비용 | 판단 |
|---|---|---|---|
| Oracle Cloud Always Free | ARM(Ampere A1) 2 OCPU, 12GB, 블록 스토리지 200GB | 무료 | 처음 고른 곳. ARM 이미지 동작도 확인했지만(아래) 7월·10월 모두 "out of host capacity"로 서버 생성 실패 |
| Hetzner CPX42, 싱가포르 | 8 vCPU(AMD x86), 16GB, 320GB NVMe | €93.99/월 | 유료 대안. 코어가 많아 업로드 처리가 빠르다 |
| Hetzner CPX42, 유럽 | 같음 | €69.99/월 | 지연이 상관없을 때 |
| Hetzner CX43(8 vCPU, 16GB, €16.49) | | | 2026-10-25 기준 "판매 중지" |
| Hugging Face Spaces 무료 CPU(16GB) | | | Docker Space는 이제 유료 플랜이 필요하다(공식 문서) |
| AWS·GCP·Azure 상시 무료 | 메모리 1GB 수준 | | 실측 최대치를 감당하지 못한다 |
| Railway | 사용량 과금: RAM $10/GB·월, vCPU $20/월 | | AX 때 쓰던 곳. 검색 엔진 볼륨 권한 문제가 있었다 |

가격·조건은 각 공식 페이지(2026-10-25 확인) 기준이다.

**Oracle 무료 서버에서 알아둘 것** (공식 문서 "Always Free Resources")
- 회수 조건: 7일 동안 CPU(95분위)·네트워크·메모리 사용률이 **모두** 20% 미만이면 유휴로 보고 회수할 수 있다. 이 구성은 평소에도
  메모리를 약 3GB(OpenSearch 2.0GiB + 앱 0.8GiB, 12GB의 약 25%) 쓰므로 메모리 조건에 걸리지 않는다.
- 생성 실패: 홈 리전에 무료 셰이프가 부족하면 "out of host capacity"가 난다. 2026년 7월에 실제로 겪었다. 잠시 뒤 다시 시도하거나 다른 가용 영역을 고른다.
- 2 OCPU라 문서 파싱·임베딩은 x86 16코어 노트북보다 느리다. 검색 자체는 가볍다.

**ARM 확인 (2026-10-25)**: 같은 `Dockerfile`을 `docker buildx --platform linux/arm64`로 빌드했다. 설치된 파이썬 패키지 134개의 버전이
x86 이미지와 같았고(`backend/requirements.lock`), 에뮬레이션으로 띄운 ARM 컨테이너에서 스캔 PDF 한국어 OCR → KURE-v1 임베딩(오프라인)
→ OpenSearch 색인 → 유사 검색까지 동작했다. 기반 이미지(python, node, OpenSearch 3.9.0, Postgres 17.11, Caddy 2.11.7)는 모두 arm64를 제공한다.

**무료 도메인**: `<IP를 대시로>.nip.io`(예: `203-0-113-10.nip.io`, 문서 예시용 IP 대역)가 그 IP로 연결되고 Let's Encrypt 인증서도 받을 수 있다(nip.io 공식 페이지).
`.io`가 공개 접미사 목록에 있어 Google 로그인 JavaScript 원본 규칙(HTTPS, IP 주소 불가, 공개 접미사 TLD)에도 맞는다.

### 실제로 올릴 때 순서

1. 서버 준비: Ubuntu Server(24.04 또는 26.04) 설치, OpenSSH 서버 켜기. 클라우드라면 공개 IP와 SSH 공개키를 등록한다
2. 외부 포트: 클라우드는 보안 목록(또는 NSG)에, 집 서버는 공유기 포트포워딩(또는 UPnP)으로 TCP 80·443을 서버로 넘긴다
3. 코드 복사 후 서버에서 `sudo bash deploy/setup-ubuntu.sh` — Docker 설치와 서버 안 방화벽(iptables) 80·443 개방
   (Oracle Ubuntu 이미지는 iptables가 22번만 허용해서 넣은 단계다. 일반 Ubuntu 26.04에서도 그대로 동작하는 것을 확인했다)
4. `deploy/.env` 작성: `DOMAIN=<IP를 대시로>.nip.io`(또는 자기 도메인), `POSTGRES_PASSWORD`·`SESSION_SECRET`은 긴 무작위 값
5. Google Cloud Console OAuth 클라이언트에 `https://<DOMAIN>`을 승인된 JavaScript 원본으로 추가
6. `docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build` (ARM 서버에서는 그 자리에서 ARM 이미지로 빌드된다)

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
- 2026-10-26 집 노트북 서버 배포: Ubuntu Server 26.04에서 `setup-ubuntu.sh`로 Docker를 설치하고 compose 4개 서비스를
  띄웠다. Let's Encrypt 인증서 발급, HTTP→HTTPS 전환, 비로그인 API 401을 외부 주소로 확인했다. 서버 안에서 업로드→파싱→색인→
  유사 검색→삭제까지 돌려 DB 청크 수와 검색 인덱스 청크 수가 일치하는 것을 확인했다. 대기 메모리 3.1GiB / 7.0GiB.

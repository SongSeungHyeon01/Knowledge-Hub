# km-platform

사내 지식관리 플랫폼 (아산 AX 3조) — 폐쇄망 문서 검색 MVP

HWP·PPT·스캔 PDF 등 포맷이 섞이고 파일명 규칙이 없는 R&D 중소기업 환경에서,
신입이 선배 없이도 **내용 기반으로 문서를 검색**하는 로컬(폐쇄망) 도구입니다.

---

## 팀 / 폴더 구조

각자 **정해진 이름의 최상위 폴더** 안에서만 작업합니다.

| 담당 | 폴더 | 영역 |
|---|---|---|
| 윤준서 ① | `parser/` | PDF 파싱 (PyMuPDF + EasyOCR) |
| 송승현 ② | `hwp_postprocess/` | HWP 처리 + 마크다운 후처리 |
| 김태훈 ③ | `search/` | 임베딩 + 하이브리드 검색엔진 |
| 김기빈 ④ | `backend/`, `frontend/` | 백엔드(FastAPI, 업로드 받기) + 화면 |

> 청킹 담당(②/③)과 DOCX·PPTX·XLSX 담당은 **아직 미정**입니다. (회의 안건 참고)

---

## 브랜치 / 작업 방식

| 브랜치 | 담당 |
|---|---|
| `main` | 안정 버전 — **직접 푸시 금지**, 합칠 때 PR로만 |
| `feature/parser` | 윤준서 |
| `feature/hwp` | 송승현 |
| `feature/search` | 김태훈 |
| `feature/backend` | 김기빈 (`backend/` + `frontend/` 둘 다 여기서) |

### 작업 순서
1. **자기 브랜치**를 선택한다 (예: 김태훈 → `feature/search`)
2. 그 안에 **자기 폴더**를 만들고 코드를 올린다 (예: `search/`)
3. 작업이 일단락되면 **`main`으로 Pull Request(PR)**를 올린다
4. PR이 합쳐지면 `main`에 자기 폴더가 추가된다

---

## ⚠️ 꼭 지킬 규칙

- **폴더 이름은 위 표 그대로** — 대소문자·철자 하나도 바꾸지 말 것
  (`parser` O / `Parser`·`parsers`·`parser_module` X — 다르게 지으면 합칠 때 따로 놂)
- **`main`에 직접 푸시하지 말 것** — 반드시 자기 `feature/...` 브랜치에 올리고 PR로 합치기
- **공용 파일(`README.md`, `.gitignore`)은 건드리지 말 것**
  — 여러 명이 같이 고치면 충돌(conflict)이 납니다. 자기 내용은 자기 폴더 안에만.
- **무거운 파일은 올리지 말 것** — 가상환경(`venv/`), 모델 파일, 캐시, 더미 데이터 등은
  `.gitignore`가 자동으로 걸러주지만, 실수로 올리지 않도록 주의

---

## 올리는 법 (Claude Code 기준)

1. 레포를 PC에 받기 (clone) — Claude Code에 시키면 됩니다
2. **자기 브랜치로 전환** 후, 자기 폴더에 코드 정리
3. Claude Code에게 "자기 브랜치에 커밋·푸시"를 지시
4. 깃허브에서 **`main`으로 PR 생성**

> 처음 한 번은 깃허브 인증(`gh auth login` 등)이 필요합니다. 본인 계정으로 직접 인증하세요.

---

## 기술 스택 (요약)

- **파싱**: PyMuPDF + EasyOCR(PDF) / pyhwp + LibreOffice(HWP)
- **검색**: BM25(rank-bm25 + kiwipiepy) + 벡터(MiniLM) 하이브리드 — Vector DB 미정
- **백엔드/화면**: FastAPI + SQLite / React
- **요약(보조)**: Ollama + Gemma 3 4B *(프로토타입 포함 여부 미정)*
- 전부 **CPU 전용** · 외부 클라우드 API 미사용(폐쇄망 전제)
- 오픈소스는 **상업적 이용 가능 라이선스(MIT/Apache 2.0)**만 사용

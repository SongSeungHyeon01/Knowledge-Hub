# codebase-memory-mcp 적용 검토

출처: [github.com/DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)

## 이게 뭔가

Claude Code 같은 코딩 에이전트가 리포를 매번 grep/파일 읽기로 탐색하는 대신, 리포 전체를
미리 그래프 DB(SQLite+FTS5)로 색인해두고 그래프 질의로 구조를 파악하게 해주는 MCP 서버다.

- 100% 로컬 처리, C로 작성된 단일 바이너리(런타임 의존성 없음), tree-sitter 158개 언어
  파서 내장, 내장 임베딩 모델(nomic-embed-code)로 시맨틱 검색도 지원
- 노드: Project/Package/Folder/File/Module/Class/Function/Method/Route 등,
  엣지: CALLS/IMPORTS/DEFINES/HTTP_CALLS/SIMILAR_TO 등 15종 이상
- 성능 주장(자체 벤치마크): 리눅스 커널(2800만 줄) 색인 3분, 그래프 질의 <1ms, 파일별
  grep 대비 토큰 99.2% 절감(41.2만 토큰 → 3,400 토큰)
- MIT 라이선스, 텔레메트리 없음, 릴리즈 바이너리는 VirusTotal 70+ 엔진 스캔 + Sigstore
  서명 + SLSA Level 3 provenance 제공

## 중요: 이건 "제품"이 아니라 "내가 이 리포를 다루는 방식"에 적용되는 도구

Elasticsearch·Docling·Oracle Cloud 작업과는 완전히 다른 층이다. Project_Knowledge Hub
자체의 검색 기능(사용자가 웹에서 문서를 찾는 기능)과는 무관하고, "Claude Code가
`C:\dev\Team-3` 리포를 탐색할 때 얼마나 효율적인가"에만 영향을 준다 — 배포 서버나 최종
사용자 경험에는 아무 변화가 없다.

## 적용하면 뭐가 달라지나

**설치**: 1줄 설치 스크립트(Windows는 PowerShell)로 43개 코딩 에이전트를 자동 감지해
MCP 설정에 등록. 리포별 최초 1회 `index_repository`로 색인(리포 크기에 비례한 시간).

**작업 방식 변화 (이번 세션 기준 구체적 비교)**:
이번 세션에서 실제로 여러 번 Grep→Read를 반복해서 답을 찾은 질문들 —
"`실제_검색_실행`이 어디서 정의됐는지", "`_get_vec_index`를 누가 호출하는지",
"department 필터가 검색 흐름의 어느 지점에서 적용되는지" — 이런 건 그래프 질의
(`search_graph`/`trace_path`) 한 번으로 답이 나온다. `main.py`처럼 2,000줄이 넘는
단일 파일을 다룰 때 특히 체감 차이가 큼(지금은 라인 번호를 Grep으로 찾고 그 주변을
Read하는 식으로 여러 번 왕복).

절감 폭은 리포 크기에 비례한다 — 우리 리포(백엔드+프론트엔드, 수만 줄대)는
벤치마크의 리눅스 커널(2800만 줄)급은 아니므로 그 정도로 극단적인 절감은 아니겠지만,
"어디 있는지 찾기" 성격의 왕복이 줄어드는 방향은 리포 크기와 무관하게 동일하다.

**팀 공유**: `.codebase-memory/graph.db.zst`(zstd 압축 스냅샷)를 커밋해두면 팀원 3명이
각자 컴퓨터에서 다시 색인할 필요 없이 동일한 그래프를 공유한다 — 각자 Claude Code로
작업하는 지금 구조에 그대로 맞는다.

**안 달라지는 것**: 제품 코드·아키텍처·배포 파이프라인은 전혀 바뀌지 않는다.
Elasticsearch/Docling/Oracle Cloud 이전 작업과 완전히 독립적이라 병행 가능하다.

**리스크**: 서드파티 바이너리를 로컬에서 실행하는 것이므로, 설치 시 서명·provenance
확인 정도는 해볼 만하다(위 근거대로 자체적으로는 검증 체계를 갖췄다고 밝힘). 비용은
0원 — 별도 API 키나 클라우드 요금이 붙지 않는다.

## 결론

제품 아키텍처와는 무관한 "개발 도구" 레벨 개선이다. `main.py`처럼 리포가 계속 커지는
추세를 고려하면 탐색 효율 이득이 시간이 갈수록 커질 것으로 보인다. 지금 당장 급한
작업은 아니지만, ES/Docling 마이그레이션과 별개로 부담 없이 설치해볼 수 있다.

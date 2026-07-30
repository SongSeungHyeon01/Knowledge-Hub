FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.0

# 한국어 형태소 분석기 (BM25 키워드 검색이 한/영 혼용 문서를 제대로 토큰화하려면 필요 —
# 기존 kiwipiepy 토크나이저를 대체하는 역할)
RUN bin/elasticsearch-plugin install --batch analysis-nori

# Railway 볼륨은 root 소유로 마운트되는데, 베이스 이미지의 기본 실행 사용자는
# 권한 없는 elasticsearch(uid 1000)라 볼륨에 쓰기가 막혀 부팅이 죽는다
# ("failed to obtain node locks... AccessDeniedException"). root로 실행하면
# docker-entrypoint.sh 자체 로직이 데이터 디렉터리를 chown한 뒤 elasticsearch로
# 다시 내려가 실행하므로(gosu) 이 문제가 해결된다 — Elastic 공식 이미지가 이
# 시나리오(볼륨 오케스트레이션 환경)를 위해 이미 지원하는 경로다.
USER root

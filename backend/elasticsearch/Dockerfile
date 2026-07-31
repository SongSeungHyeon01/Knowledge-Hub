FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.0

# 한국어 형태소 분석기 (BM25 키워드 검색이 한/영 혼용 문서를 제대로 토큰화하려면 필요 —
# 기존 kiwipiepy 토크나이저를 대체하는 역할)
RUN bin/elasticsearch-plugin install --batch analysis-nori

# [2026-07-31 정정] Railway 볼륨 권한 문제(볼륨이 root 소유로 마운트되는데
# elasticsearch 사용자는 쓰기 권한이 없어 부팅이 죽는 문제)를 "USER root로 실행 +
# docker-entrypoint.sh의 자체 chown 로직"으로 풀려고 했으나, ES 자체가 root 실행을
# 명시적으로 거부한다("can not run elasticsearch as root" — RuntimeException, 안전장치).
# 그래서 이 이미지는 볼륨 없이(영속성 없이) 돌리고, main.py의 자동 복구
# 태스크(_es_selfheal_task)가 재시작마다 Postgres에서 다시 채우는 방식으로 우회한다.

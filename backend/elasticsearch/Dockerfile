FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.0

# 한국어 형태소 분석기 (BM25 키워드 검색이 한/영 혼용 문서를 제대로 토큰화하려면 필요 —
# 기존 kiwipiepy 토크나이저를 대체하는 역할)
RUN bin/elasticsearch-plugin install --batch analysis-nori

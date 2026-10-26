# OpenSearch 3.9.0 (Apache-2.0) + 한국어 형태소 분석기 analysis-nori (Apache-2.0)
#
# [2026-10-24] Elasticsearch 8.15 → OpenSearch 교체. Elasticsearch 배포판은 Elastic License 2.0이라
# 프로젝트 라이선스 기준(허용형 오픈소스만)에 맞지 않았다. 이 이미지에는 k-NN 플러그인이 기본으로
# 들어 있고(opensearch-knn), nori만 추가로 설치한다 — 둘 다 2026-10-24에 이미지 안에서 직접 확인.
FROM opensearchproject/opensearch:3.9.0

RUN /usr/share/opensearch/bin/opensearch-plugin install --batch analysis-nori

# 로컬·사내망 전용 설정: 보안 플러그인(인증·TLS)을 끄고 HTTP로 연다. 그래서 포트를 외부에 그대로
# 열면 안 된다 — docker-compose.yml은 127.0.0.1에만 바인딩한다. 배포 환경에서는 같은 사설
# 네트워크 안에서만 접근되게 하거나, 보안 플러그인을 켜고 OPENSEARCH_URL에 인증 정보를 넣는다.
ENV discovery.type=single-node \
    DISABLE_SECURITY_PLUGIN=true \
    DISABLE_INSTALL_DEMO_CONFIG=true

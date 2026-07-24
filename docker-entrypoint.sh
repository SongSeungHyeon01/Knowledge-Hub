#!/bin/sh
# 컨테이너 시작 스크립트 (2026-07-25) — ClamAV 데몬(clamd)을 백엔드보다 먼저 띄운다.
#
# clamd는 시그니처DB(수백MB)를 메모리에 올리는 데 시간이 걸려서, uvicorn과 동시에
# 그냥 백그라운드로 던져두면 서버가 받는 첫 업로드 몇 건이 "clamd 연결 실패"로 검사를
# 건너뛴 채(main.py의 소프트-패스) 통과할 수 있다 — 아래에서 소켓이 생길 때까지
# 잠깐 기다린 뒤 uvicorn을 시작해 이 창을 최대한 줄인다.
set -e

mkdir -p /var/run/clamav /var/log/clamav
chown clamav:clamav /var/run/clamav /var/log/clamav 2>/dev/null || true

echo "[entrypoint] freshclam으로 최신 바이러스 시그니처 갱신 시도..."
freshclam --quiet || echo "[entrypoint] freshclam 실패 — 빌드 시점에 받아둔 시그니처로 계속 진행"

echo "[entrypoint] clamd 데몬 시작..."
clamd --config-file=/app/clamd.conf &

echo "[entrypoint] clamd 소켓 대기 중..."
for i in $(seq 1 60); do
    [ -S /var/run/clamav/clamd.ctl ] && { echo "[entrypoint] clamd 준비 완료"; break; }
    sleep 1
done

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"

#!/usr/bin/env bash
# 새 Ubuntu 서버(Oracle Cloud Always Free ARM 등)에 Knowledge Hub를 띄우는 준비 스크립트.
# 서버에서 한 번 실행한다:  sudo bash deploy/setup-ubuntu.sh
#
# 하는 일
#   1) Docker Engine + compose 플러그인 설치(Docker 공식 apt 저장소)
#   2) 서버 안 방화벽에서 80/443 열기 — Oracle Cloud의 Ubuntu 이미지는 iptables가 22번만 허용한다.
#      (클라우드 콘솔의 보안 목록/NSG에서도 80·443 인바운드를 따로 열어야 한다)
#   3) 접속 계정을 docker 그룹에 추가
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "sudo로 실행하세요: sudo bash $0" >&2
  exit 1
fi

# 1) Docker Engine (https://docs.docker.com/engine/install/ubuntu/)
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 2) 서버 안 방화벽: 80/443 허용 — INPUT 체인의 첫 REJECT 규칙보다 앞에 넣고, 재부팅 후에도 유지
reject_pos=$(iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')
for port in 443 80; do
  if ! iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null; then
    iptables -I INPUT "${reject_pos:-1}" -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  fi
done
iptables -L INPUT --line-numbers -n
if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save
fi

# 3) sudo 없이 docker를 쓰도록
if [ -n "${SUDO_USER:-}" ]; then
  usermod -aG docker "$SUDO_USER"
fi

docker --version
docker compose version
echo
echo "다음 단계: deploy/.env 작성 후"
echo "  docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build"

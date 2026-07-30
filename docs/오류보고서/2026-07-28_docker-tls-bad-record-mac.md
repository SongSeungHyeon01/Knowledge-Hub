# Docker Desktop에서 Elasticsearch 이미지 pull 시 "tls: bad record MAC" 반복 실패

- **발생 시각**: 2026-07-28
- **위치**: `backend/elasticsearch/docker-compose.yml` — `docker.elastic.co/elasticsearch/elasticsearch:8.15.0` 이미지 pull (`docker compose up -d`)
- **상태**: 해결됨

## 증상

`docker compose up -d`로 Elasticsearch 컨테이너를 기동하려는데, 631MB짜리 대용량
레이어(`dc9db6ea5ff2`)를 받는 도중 매번 다음 오류로 실패한다:

```
failed to copy: local error: tls: bad record MAC
```

실패 지점은 매번 다르다(1MB~76MB 사이 무작위) — 레이어 다운로드가 절대 완료되지
않고, 20회 넘게 재시도해도 같은 지점 근처에서 계속 끊긴다. 작은 레이어들은 전부
정상적으로 받아진다(문제는 큰 레이어에서만 재현됨).

## 원인 조사

1. **처음 가설: nori 플러그인 다운로드 문제** — 커스텀 Dockerfile에서
   `elasticsearch-plugin install analysis-nori` 실행 시 같은 오류가 나서, 처음엔
   플러그인 아티팩트 호스트(artifacts.elastic.co)만의 문제로 의심함.
   → **틀렸음**: 커스텀 Dockerfile을 버리고 base 이미지를 그대로 pull해도 동일한
   오류가 같은 레이어에서 재현됨. 즉 특정 호스트 문제가 아니라 더 일반적인 네트워크
   문제.
2. **Docker Desktop 데몬 재시작** — 이전 세션에서 "데몬이 안 떠 있음" 문제를
   Docker Desktop 재시작으로 해결한 전례가 있어 동일하게 시도.
   → **효과 없음**: 재시작 후에도 동일한 지점 근처에서 동일 오류 재현.
3. **WSL 가상 이더넷 어댑터 체크섬 오프로드** — Windows + Docker Desktop(WSL2
   백엔드) 조합에서 대용량 다운로드 시 `tls: bad record MAC`이 나는 건 널리 알려진
   패턴으로, WSL 가상 NIC의 TCP/UDP 체크섬 오프로드가 원인인 경우가 많다.
   `Get-NetAdapterChecksumOffload -Name "vEthernet (WSL (Hyper-V firewall))"` 확인
   결과 `TcpIPv4Enabled/UdpIPv4Enabled/IpIPv4Enabled` 전부 `RxTxEnabled`(켜짐) —
   증상과 일치하는 상태였음.

## 근본 원인

✅ 확인됨: WSL 가상 이더넷 어댑터만 끄고 재시도했을 때는 **여전히 같은 지점 근처에서
동일 오류가 재현**됐다 — 즉 WSL vEthernet 하나만으론 원인이 아니었다(가설 기각).
실제 물리 네트워크 어댑터인 **"Ethernet 2" (Realtek USB GbE Family Controller)**도
체크섬 오프로드가 켜져 있었고, 이것까지 함께 끈 뒤에는 631MB 레이어가 끝까지
정상적으로 받아졌다. Realtek USB 이더넷 컨트롤러는 하드웨어 체크섬/세그멘테이션
오프로드가 대용량 전송을 손상시키는 사례가 널리 알려져 있어(TLS 레코드 무결성
검증(MAC)이 깨지는 형태로 나타남), 이 어댑터가 실제 근본 원인이었던 것으로 확인됨.
WSL vEthernet 쪽 오프로드도 같이 꺼둔 상태라 둘 다의 조합 효과일 가능성은 남아있지만,
Realtek 어댑터가 핵심 원인이라는 쪽에 더 무게가 실린다.

## 해결 방법 / 다음 시도

관리자 권한 PowerShell에서 두 어댑터 모두 체크섬 오프로드를 껐다:

```powershell
Disable-NetAdapterChecksumOffload -Name "vEthernet (WSL (Hyper-V firewall))" -Confirm:$false
Disable-NetAdapterChecksumOffload -Name "Ethernet 2" -Confirm:$false
```

이후 `backend/elasticsearch`에서 `docker compose up -d` 재시도 → 631MB 레이어까지
포함해 전체 이미지 pull 성공, 컨테이너 정상 기동(`curl localhost:9200/_cluster/health`
→ `"status":"green"`) 확인.

## 재발 방지

- 이 머신에서 앞으로 대용량 Docker 이미지를 받을 때 같은 오류가 재현되면, 매번
  체크섬 오프로드부터 확인한다(재시도만 반복하지 말 것 — 15회 이상 재시도해도 전혀
  나아지지 않았음이 이번에 확인됨).
- 되돌리기: 문제가 해결된 후 필요하면 `Enable-NetAdapterChecksumOffload -Name "vEthernet (WSL (Hyper-V firewall))"`로 다시 켤 수 있다(성능 오프로드 기능이므로 끈 상태로 둬도
  기능상 문제는 없음, 약간의 CPU 오버헤드만 늘어남).

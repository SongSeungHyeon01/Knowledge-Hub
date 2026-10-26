"""retry_launch.py — Oracle Cloud Ampere A1 Always Free 인스턴스 생성 자동 재시도

용량 부족("Out of host capacity")으로 실패하면 일정 간격으로 계속 재시도하고,
그 외의 오류(설정 오류 등)가 나면 즉시 멈춘다. 성공하면 인스턴스 OCID와
퍼블릭 IP를 출력하고 종료한다.
"""

import sys
import time

import oci

COMPARTMENT_ID = None  # None이면 tenancy(root) 사용
AVAILABILITY_DOMAIN = "Xjyc:AP-SINGAPORE-1-AD-1"
SUBNET_ID = "ocid1.subnet.oc1.ap-singapore-1.aaaaaaaawsistjqpvh5jhyu233sbmkdpcqizjgmoethgl5ox3scgxg73qnga"  # knowledge-hub-subnet
# [2026-10-25] Oracle Linux → Ubuntu 24.04로 변경. deploy/setup-ubuntu.sh가 apt 기반이라서다.
# 이미지 OCID는 주기적으로 새 빌드가 나오므로 고정하지 않고 실행 시 최신 것을 찾는다.
IMAGE_OS = "Canonical Ubuntu"
IMAGE_OS_VERSION = "24.04"
BOOT_VOLUME_GBS = 100  # 기본 47GB는 Docker 이미지(모델 포함)+OpenSearch 데이터에 빠듯함. Always Free 합계 200GB 이내
SHAPE = "VM.Standard.A1.Flex"
OCPUS = 2
MEMORY_GBS = 12
# [2026-07-29] Oracle이 2026-06부터 Free Trial 계정의 Always Free Ampere 한도를
# 4 OCPU/24GB에서 2 OCPU/12GB로 축소했다는 보도가 있어(InfoQ) 우선 이 값으로 낮춰
# 시도한다. Pay As You Go로 업그레이드하면 4/24가 복구될 수 있다는 정보도 있음.
DISPLAY_NAME = "Knowledge-Hub-Server"
SSH_PUBLIC_KEY_PATH = r"C:\Users\ssh96\.ssh\oracle_knowledge_hub.pub"

RETRY_INTERVAL_SEC = 75  # 45초는 절반이 rate limit로 낭비돼서 늘림


def wait_public_ip(config, compute, compartment_id, instance_id):
    """인스턴스가 RUNNING이 될 때까지 기다린 뒤 퍼블릭 IP를 돌려준다."""
    oci.wait_until(compute, compute.get_instance(instance_id), "lifecycle_state", "RUNNING", max_wait_seconds=900)
    network = oci.core.VirtualNetworkClient(config)
    attachments = compute.list_vnic_attachments(compartment_id, instance_id=instance_id).data
    return network.get_vnic(attachments[0].vnic_id).data.public_ip


def main():
    config = oci.config.from_file()
    compartment_id = COMPARTMENT_ID or config["tenancy"]
    compute = oci.core.ComputeClient(config)

    with open(SSH_PUBLIC_KEY_PATH, "r") as f:
        ssh_key = f.read().strip()

    images = compute.list_images(
        compartment_id,
        operating_system=IMAGE_OS,
        operating_system_version=IMAGE_OS_VERSION,
        shape=SHAPE,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    if not images:
        print(f"{IMAGE_OS} {IMAGE_OS_VERSION} 이미지를 찾지 못했습니다.")
        sys.exit(1)
    image = images[0]
    print(f"이미지: {image.display_name}")

    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=compartment_id,
        availability_domain=AVAILABILITY_DOMAIN,
        display_name=DISPLAY_NAME,
        shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=OCPUS,
            memory_in_gbs=MEMORY_GBS,
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=image.id,
            boot_volume_size_in_gbs=BOOT_VOLUME_GBS,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID,
            assign_public_ip=True,  # 콘솔 위저드에서 안 켜지던 그 옵션 — API로는 직접 지정 가능
        ),
        metadata={"ssh_authorized_keys": ssh_key},
    )

    attempt = 0
    while True:
        attempt += 1
        print(f"[시도 {attempt}] {time.strftime('%Y-%m-%d %H:%M:%S')} — launch_instance 호출 중...")
        try:
            resp = compute.launch_instance(details)
            instance = resp.data
            print(f"성공! instance_id={instance.id}")
            print(f"퍼블릭 IP: {wait_public_ip(config, compute, compartment_id, instance.id)}")
            break
        except oci.exceptions.ServiceError as e:
            msg = str(e.message or "")
            if "Out of capacity" in msg or "Out of host capacity" in msg:
                print(f"  -> 용량 부족, {RETRY_INTERVAL_SEC}초 후 재시도")
            elif e.status == 429 or "Too many requests" in msg:
                wait = RETRY_INTERVAL_SEC * 2
                print(f"  -> 요청 과다(rate limit), {wait}초 후 재시도")
                time.sleep(wait)
                continue
            else:
                print(f"  -> 예상 못한 오류(status={e.status}): {msg}")
                print("  -> 설정 문제일 수 있어 재시도를 멈춥니다. 확인 후 다시 실행하세요.")
                sys.exit(1)
            time.sleep(RETRY_INTERVAL_SEC)


if __name__ == "__main__":
    main()

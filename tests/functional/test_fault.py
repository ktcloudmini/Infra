import time
import pytest
import requests

from tests.utils import (
    get_healthy_instance_ids,
    is_service_available,
)

pytestmark = pytest.mark.fault

# ---- Constants
MIN_HEALTHY_INSTANCES = 2

SETUP_WAIT_SECONDS = 300            # 5 min
DETECT_TIMEOUT_SECONDS = 180        # 3 min
RECOVERY_TIMEOUT_SECONDS = 600      # 10 min

POLL_INTERVAL_SHORT = 5     # 상태변화 감지용
POLL_INTERVAL_LONG = 10     # 느린 복구 대기용 

AVAILABILITY_THRESHOLD_PERCENT = 95


# ---- Fixtures
@pytest.fixture(autouse=True)
def wait_for_stable_state(elbv2_client, tg_arn):
    """
    테스트 시작 전 시스템 안정화 대기
    - Target Group 내 healthy 인스턴스가 최소 2대 이상일 때까지 대기
    """
    if not tg_arn:
        return

    print("\n[Setup] Waiting for stable system state...", end="", flush=True)
    start = time.time()

    while time.time() - start < SETUP_WAIT_SECONDS:
        healthy_ids = get_healthy_instance_ids(elbv2_client, tg_arn)
        if len(healthy_ids) >= MIN_HEALTHY_INSTANCES:
            print(" done.")
            return

        time.sleep(POLL_INTERVAL_LONG)
        print(".", end="", flush=True)

    pytest.skip(
        f"System is unstable: healthy instances < {MIN_HEALTHY_INSTANCES} "
        f"after {SETUP_WAIT_SECONDS}s."
    )


# ---- Tests
def test_app_fault_recovery(alb_url, elbv2_client, tg_arn):
    """
    [Scenario A] Application fault recovery (/kill)

    목적:
    - ALB Health Check이 애플리케이션 장애를 감지하는지
    - ASG가 자동으로 인스턴스를 교체하는지
    - 장애 중 서비스 가용성 확인
    """
    print("\n[Scenario A] Application fault recovery (/kill)")

    # ---- Initial healthy check
    initial_ids = get_healthy_instance_ids(elbv2_client, tg_arn)
    print(f"[Step 1] Initial healthy instances: {len(initial_ids)} {initial_ids}")

    # ---- Inject App fault
    print("[Step 2] Injecting fault via /kill")
    try:
        requests.get(f"{alb_url}/kill", timeout=1)
    except Exception:
        pass  # fault injection 목적이므로 예외 무시

    # Step 3: ALB 장애 감지 + 가용성 측정
    print(
        f"[Step 3] Waiting for ALB to detect unhealthy target "
        f"(timeout={DETECT_TIMEOUT_SECONDS}s)...",
        end="",
        flush=True,
    )

    start_detect = time.time()
    success_rate = 0
    check_rate = 0

    while time.time() - start_detect < DETECT_TIMEOUT_SECONDS:
        current_ids = get_healthy_instance_ids(elbv2_client, tg_arn)

        if len(current_ids) < len(initial_ids):
            print("\n-> Fault detected by ALB.")
            break

        # ---- availability check
        if is_service_available(alb_url):
            success_rate += POLL_INTERVAL_SHORT 
        check_rate += POLL_INTERVAL_SHORT

        time.sleep(POLL_INTERVAL_SHORT)
        print(".", end="", flush=True)
    else:
        pytest.fail(
            f"ALB did not detect application fault within "
            f"{DETECT_TIMEOUT_SECONDS}s."
        )

    # ---- ASG Recovery check
    print(
        f"[Step 4] Waiting for ASG recovery "
        f"(timeout={RECOVERY_TIMEOUT_SECONDS}s)...",
        end="",
        flush=True,
    )

    start_recover = time.time()

    while time.time() - start_recover < RECOVERY_TIMEOUT_SECONDS:
        current_ids = get_healthy_instance_ids(elbv2_client, tg_arn)

        if len(current_ids) >= len(initial_ids):
            elapsed = int(time.time() - start_recover)
            print(f"\n-> Recovery completed in {elapsed}s")

            if check_rate > 0:
                availability = (success_rate / check_rate) * 100
                print(
                    f"   Availability during recovery: "
                    f"{availability:.1f} %" #({success_rate}/{check_rate})"
                )   

                if availability < AVAILABILITY_THRESHOLD_PERCENT:
                    print("   [WARN] Some requests failed during recovery.")
                else:
                    print("   [PASS] High availability maintained.")

            return

        #---- availability check
        if is_service_available(alb_url):
            success_rate += POLL_INTERVAL_LONG
        check_rate += POLL_INTERVAL_LONG

        time.sleep(POLL_INTERVAL_LONG)
        print(".", end="", flush=True)

    pytest.fail(
        f"Service did not recover within {RECOVERY_TIMEOUT_SECONDS}s."
    )



def test_infra_fault_recovery(asg_client, elbv2_client, tg_arn, alb_url):
    """
    [Scenario B] Infrastructure fault recovery (Terminate Instance)

    목적:
    - 인스턴스 강제 종료 시에도 서비스가 유지되는지
    - ASG Self-Healing 동작 여부 검증
    """
    print("\n[Scenario B] Infrastructure fault recovery (Terminate Instance)")

    # Step 1: 희생 인스턴스 선정 및 종료
    initial_ids = get_healthy_instance_ids(elbv2_client, tg_arn)
    victim_id = initial_ids[0]
    print(f"[Step 1] Terminating instance: {victim_id}")

    asg_client.terminate_instance_in_auto_scaling_group(
        InstanceId=victim_id,
        ShouldDecrementDesiredCapacity=False,
    )

    # Step 2: 복구 및 가용성 모니터링
    print(
        f"[Step 2] Monitoring recovery & availability "
        f"(timeout={RECOVERY_TIMEOUT_SECONDS}s)...",
        end="",
        flush=True,
    )

    start = time.time()
    success_count = 0
    check_count = 0

    while time.time() - start < RECOVERY_TIMEOUT_SECONDS:
        current_ids = get_healthy_instance_ids(elbv2_client, tg_arn)

        # Self-healing 완료 조건
        if victim_id not in current_ids and len(current_ids) >= len(initial_ids):
            elapsed = int(time.time() - start)
            print(f"\n-> Self-healing completed in {elapsed}s")

            if check_count > 0:
                availability = (success_count / check_count) * 100
                print(
                    f"   Availability during recovery: "
                    f"{availability:.1f}% ({success_count}/{check_count})"
                )

                if availability < AVAILABILITY_THRESHOLD_PERCENT:
                    print("   [WARN] Some requests failed during recovery.")
                else:
                    print("   [PASS] High availability maintained.")

            return

        # 가용성 체크
        if is_service_available(alb_url):
            success_count += 1
        check_count += 1

        time.sleep(POLL_INTERVAL_SHORT)
        print(".", end="", flush=True)

    pytest.fail(
        f"Infrastructure recovery failed within {RECOVERY_TIMEOUT_SECONDS}s."
    )

import time
import threading

import pytest
import requests

from tests.utils import now_str, get_asg_desired_capacity, get_healthy_target_instance_ids


pytestmark = pytest.mark.scaling

# ---- Constants
LOAD_THREAD_COUNT = 6 ##Flask 6
LOAD_SEC = 60
RETRIGGER_EVERY = 20

SCALE_OUT_TIMEOUT_SECONDS = 600   # 10 minutes
SCALE_IN_TIMEOUT_SECONDS = 1500    # 25 minutes

SCALE_OUT_POLL_INTERVAL = 15
SCALE_IN_POLL_INTERVAL = 30

WORK_REQUEST_TIMEOUT = 8  

# ---- Test-only Load Generator
def load_generator(base_url, stop_event):
    """
    /work 엔드포인트를 호출하여 부하 생성
    """
    while not stop_event.is_set():
        try:
            requests.get(f"{base_url}/work",params={'sec':LOAD_SEC},timeout=WORK_REQUEST_TIMEOUT)
        except Exception:
            pass
    
    stop_event.wait(RETRIGGER_EVERY)


# ---- Tests
def test_asg_scaling_lifecycle(alb_url, asg_client, asg_name, elbv2_client, tg_arn):
    """
    ASG Scaling Lifecycle 검증
    - Scale-out 발생 여부
    - 부하 제거 후 Scale-in 여부
    """
    if not asg_name:
        pytest.skip("ASG name is not configured.")

    # ---- Step 1. Initial state
    initial_capacity = get_asg_desired_capacity(asg_client, asg_name)
    initial_healthy_ids = get_healthy_target_instance_ids(elbv2_client, tg_arn)

    print(f"\n[{now_str()}] [INFO] Initial capacity: {initial_capacity}")
    print(f"[{now_str()}] [INFO] Initial healthy targets: {initial_healthy_ids}")
    print(f"[{now_str()}] [INFO] Target URL: {alb_url}/work")

    # ---- Step 2. Start load
    stop_event = threading.Event()
    threads = [
        threading.Thread(
            target=load_generator,
            args=(alb_url, stop_event),
            daemon=True,
        )
        for _ in range(LOAD_THREAD_COUNT)
    ]

    for t in threads:
        t.start()

    print(f"[{now_str()}] [INFO] Load started ({LOAD_THREAD_COUNT} threads)")

    try:
        # ---- Step 3. Scale-out decision check


        print(
            f"[{now_str()}] [CHECK] Waiting for scale-out decision "
            f"(timeout={SCALE_OUT_TIMEOUT_SECONDS}s)...",
            end="",
            flush=True,
        )
        scale_out_capacity = None
        current_capacity = initial_capacity
        start_out = time.time()
        while time.time() - start_out < SCALE_OUT_TIMEOUT_SECONDS:
            current_capacity = get_asg_desired_capacity(asg_client, asg_name)

            if current_capacity > initial_capacity:
                scale_out_capacity = current_capacity
                print(
                    f"\n[{now_str()}] [PASS] Scale-out decision detected "
                    f"({initial_capacity} -> {current_capacity})"
                )
                break

            time.sleep(SCALE_OUT_POLL_INTERVAL)
            print(".", end="", flush=True)

        if scale_out_capacity is None:
            pytest.fail(f"Scale-out not detected within {SCALE_OUT_TIMEOUT_SECONDS}s (current={current_capacity})")
        
        # ---- Step 4. Scale-out check (Healthy target 실제로 증가했는지)

        print(f"[{now_str()}] [CHECK] Waiting for new healthy targets...", end="",flush=True,)
        scale_out_healthy_ids = None
        start_health = time.time()
        while time.time() - start_health < SCALE_OUT_TIMEOUT_SECONDS:
            current_ids = get_healthy_target_instance_ids(elbv2_client, tg_arn)

            if len(current_ids) > len(initial_healthy_ids):
                scale_out_healthy_ids = current_ids
                new_ids = set(current_ids) - set(initial_healthy_ids)
                print(
                    f"\n[{now_str()}] [PASS] New healthy targets detected: {list(new_ids)}"
                )
                break

            time.sleep(SCALE_OUT_POLL_INTERVAL)
            print(".", end="", flush=True)
        if scale_out_healthy_ids is None:
            pytest.fail(
                "Scale-out detected but no new healthy targets joined ALB "
                f"within {SCALE_OUT_TIMEOUT_SECONDS}s"
            )
    finally:    
        # ---- Step 5. Stop load
        print(f"\n[{now_str()}] [INFO] Stopping load generation")
        stop_event.set()
        for t in threads:
            t.join()

    # ---- Step 6. Scale-in decision check (DesiredCapacity 감소)
    print(
        f"[{now_str()}] [CHECK] Waiting for scale-in decision (timeout={SCALE_IN_TIMEOUT_SECONDS}s)...",
        end="", flush=True,
    )
    start_in = time.time()
    scale_in_capacity = None
    while time.time() - start_in < SCALE_IN_TIMEOUT_SECONDS:
        current_capacity = get_asg_desired_capacity (asg_client, asg_name)
        if current_capacity < scale_out_capacity:
            scale_in_capacity = current_capacity
            print(
                f"\n[{now_str()}] [PASS] Scale-in decision detected "
                f"({scale_out_capacity} -> {current_capacity})"
            )
            break
        time.sleep(SCALE_IN_POLL_INTERVAL)
        print(".", end="", flush=True)
    if scale_in_capacity is None:
        pytest.fail(f"Scale-in decision was not detected within {SCALE_IN_TIMEOUT_SECONDS}")
    
    # ---- Step 7. Scale-in check (Healthy target 감소)
    print(
        f"[{now_str()}] [CHECK] Waiting for # of healthy targets to drop (timeout={SCALE_IN_TIMEOUT_SECONDS}s)...",
        end="", flush=True,
    )
    start_health_in = time.time()

    while time.time() - start_health_in < SCALE_IN_TIMEOUT_SECONDS:
        current_ids = get_healthy_target_instance_ids(elbv2_client, tg_arn)

        if (len(current_ids) < len(scale_out_healthy_ids)):
            removed_ids = set(scale_out_healthy_ids) - set(current_ids)
            print(
                f"\n[{now_str()}] [PASS] Scale-in detected removed_targets={list(removed_ids)})"
            )
            return
        time.sleep(SCALE_IN_POLL_INTERVAL)
        print(".", end="", flush=True)
    pytest.fail(f"Scale-in decision happened, but healthy targets did not decrease within {SCALE_IN_TIMEOUT_SECONDS}")
    
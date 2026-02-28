import os
import json
import time
from pathlib import Path

from locust import (
    HttpUser,
    task,
    between,
    LoadTestShape,
    constant,
)

from dotenv import load_dotenv

# locust_metrics로 분리된 로직 임포트
from locust_metrics import MetricsTracker, _now_str

# for terminal output when --only-summary enabled
import builtins, sys
orig_print = builtins.print
def stderr_print(*args, **kwargs):
    kwargs.setdefault('file', sys.stderr)
    kwargs.setdefault('flush', True)
    return orig_print(*args, **kwargs)
builtins.print = stderr_print


# gevent (Locust 내부에서 사용)
try:
    from gevent import sleep
except Exception:
    def sleep(x):  # fallback
        time.sleep(x)


# ---- 1. Configuration & Environment Loading
_CUR_DIR = Path(__file__).resolve().parent
_TESTS_DIR = _CUR_DIR.parent
_ENV_PATH = _CUR_DIR / ".env"
if not _ENV_PATH.exists():
    _ENV_PATH = _TESTS_DIR / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


def get_alb_url():
    """
    ALB URL 가져오기
    - 1) ENV: ALB_URL
    - 2) tests/infra_config.json: alb_dns_name.value
    """
    url = os.getenv("ALB_URL")
    if url:
        return url.strip()

    config_path = _TESTS_DIR / "infra_config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            v = data.get("alb_dns_name", {}).get("value")
            if v:
                return str(v).strip()
        except Exception:
            pass
    return None


class Config:
    """테스트 실행에 필요한 모든 환경 변수 및 설정값"""
    ALB_URL = get_alb_url()
    
    _obs_raw = os.getenv("OBSERVE_PATH", "/").strip()
    OBSERVE_PATH = _obs_raw if _obs_raw.startswith("/") else "/" + _obs_raw

    try:
        WORK_SEC = float(os.getenv("WORK_SEC", "5"))
    except ValueError:
        WORK_SEC = 5.0

    OBS_WAIT_MIN = float(os.getenv("OBS_WAIT_MIN", "0.5"))
    OBS_WAIT_MAX = float(os.getenv("OBS_WAIT_MAX", "1.5"))

    ENABLE_FAULT = os.getenv("ENABLE_FAULT", "0") == "1"
    FAULT_MODE = os.getenv("FAULT_MODE", "single").lower()

    ENABLE_SCALING = os.getenv("ENABLE_SCALING", "0") == "1"
    SCALING_WEIGHT = int(os.getenv("SCALING_WEIGHT", "20"))
    SCALING_DURATION_SEC = int(os.getenv("SCALING_DURATION_SEC","0"))


    USE_STEP_SHAPE = os.getenv("USE_STEP_SHAPE", "0") == "1"
    STEP_TIME = int(os.getenv("STEP_TIME", "180"))
    STEP_USERS = int(os.getenv("STEP_USERS", "50"))
    SPAWN_RATE = float(os.getenv("SPAWN_RATE", "10"))
    TIME_LIMIT = int(os.getenv("TIME_LIMIT", "720"))

    SLA_P95_MS = float(os.getenv("SLA_P95_MS", "1000"))
    ENABLE_STEP_SLA_STOP = os.getenv("ENABLE_STEP_SLA_STOP", "0") == "1"

    INITIAL_HOST_WINDOW_SEC = 10.0
    KILL_ALL_REQUESTS = int(os.getenv("KILL_ALL_REQUESTS", "16"))
    KILL_ALL_RETRY_ONCE = os.getenv("KILL_ALL_RETRY_ONCE", "1") == "1"
    SLA_STEP_MIN_SAMPLES = 20
    TOP_N_FAILURES = 5
    OUTAGE_TOP_N = 10
    OUTAGE_MIN_SEC = float(os.getenv("OUTAGE_MIN_SEC", "0.10"))


# 단 한 줄로 모든 메트릭 추적 활성화
tracker = MetricsTracker(Config)


# ---- 2. User Classes

class BaseUser(HttpUser):
    abstract = True
    host = Config.ALB_URL

    if host and not host.startswith("http"):
        host = f"http://{host}"


class ObserveUser(BaseUser):
    """OBSERVE 트래픽: LB 분산/지연/실패 확인용."""
    weight = 100 - Config.SCALING_WEIGHT if Config.ENABLE_SCALING else 100
    wait_time = between(Config.OBS_WAIT_MIN, Config.OBS_WAIT_MAX)

    @task(1)
    def observe(self):
        self.client.get(Config.OBSERVE_PATH, name="OBSERVE")


class ScalingUser(BaseUser):
    """WORK 트래픽: /work 호출로 CPU load 유도 (autoscaling 트리거용)."""

    weight = Config.SCALING_WEIGHT if Config.ENABLE_SCALING else 0
    wait_time = constant(5)  # 고정 5s 간격 (WORK_SEC와 별개)
    def _idle_forever(self):
        while True:
            sleep(60)
    
    @task(1)
    def work(self):
        if Config.SCALING_DURATION_SEC>0 and tracker.test_start_ts is not None:
            elapsed = time.time() - tracker.test_start_ts
            if elapsed > Config.SCALING_DURATION_SEC:
                self._idle_forever()
        if Config.WORK_SEC:
            self.client.get(f"/work?sec={Config.WORK_SEC}", name="WORK")
        else:
            self.client.get("/work", name="WORK")


class FaultUser(BaseUser):
    """
    Fault injection user (1개만 실행 의도)
    - single: /kill 1회
    - all   : /kill을 N번 던짐 + probe 1회로 "아직 살아있으면" pass2를 1번만 더(옵션)

    주의:
    - all 모드는 unique host 추적/보장 안 함 (그냥 여러 번 보내는 방식)
    - custom 지표는 OBSERVE만 집계라서, FAULT_* / PROBE 요청은 Summary 집계에서 제외됨
    """
    if Config.ENABLE_FAULT:
        fixed_count = 1
    else:
        weight = 0

    wait_time = constant(0)

    def _idle_forever(self):
        while True:
            sleep(60)

    @task
    def inject_fault(self):
        runner = self.environment.runner

        # 모든 유저가 spawn 된 뒤 실행(대충 안정화)
        while runner.user_count < runner.target_user_count:
            sleep(0.5)
        sleep(10)

        target_mode = Config.FAULT_MODE
        mode_label = "KILL ALL" if target_mode == "all" else "SINGLE"
        print(f"[{_now_str()}] FAULT INJECTION STARTING... Mode: {mode_label}")

        if target_mode == "single":
            try:
                resp = self.client.get("/kill", name="FAULT_KILL_SINGLE")
                if resp.status_code == 200:
                    print(f"[{_now_str()}] -> Single instance terminated successfully.")
                else:
                    print(f"[{_now_str()}] -> Failed to terminate instance. Status: {resp.status_code}")
            except Exception as e:
                print(f"[{_now_str()}] -> /kill request error (single): {e}")

        elif target_mode == "all":
            print(
                f"[{_now_str()}] -> KILL ALL (all-target kill) starting: "
                f"requests_per_pass={Config.KILL_ALL_REQUESTS}, retry_once={Config.KILL_ALL_RETRY_ONCE}"
            )

            def _kill_all_pass(n: int, req_name: str) -> int:
                sent = 0
                for _ in range(max(0, n)):
                    # 세션 끊고 다시 붙기(best effort로 target 분산 유도)
                    try:
                        self.client.session.close()
                    except Exception:
                        pass

                    try:
                        self.client.get("/kill", name=req_name)
                        sent += 1
                    except Exception:
                        pass
                return sent

            # pass1
            sent1 = _kill_all_pass(Config.KILL_ALL_REQUESTS, "FAULT_KILL_ALL_PASS1")

            # probe 1회: 아직 살아있으면 pass2 한 번만 더
            probe_ok = False
            try:
                self.client.session.close()
            except Exception:
                pass
            try:
                rr = self.client.get(Config.OBSERVE_PATH, name="OBSERVE_KILL_ALL_PROBE")
                if rr is not None and rr.status_code < 400:
                    probe_ok = True
            except Exception:
                probe_ok = False

            sent2 = 0
            if probe_ok and Config.KILL_ALL_RETRY_ONCE:
                sent2 = _kill_all_pass(Config.KILL_ALL_REQUESTS, "FAULT_KILL_ALL_PASS2")

            print(f"[{_now_str()}] -> KILL ALL done: pass1_sent={sent1}, pass2_sent={sent2}, probe_ok={probe_ok}")

        self._idle_forever()


# ---- 3. Load Shape (Optional)

if Config.USE_STEP_SHAPE:

    class StepLoadShape(LoadTestShape):
        step_time = Config.STEP_TIME
        step_users = Config.STEP_USERS
        spawn_rate = Config.SPAWN_RATE
        time_limit = Config.TIME_LIMIT

        def tick(self):
            run_time = self.get_run_time()
            if run_time > self.time_limit:
                return None
            current_step = (run_time // self.step_time) + 1
            return current_step * self.step_users, self.spawn_rate
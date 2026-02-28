import time
import math
import re
from collections import defaultdict
from locust import events
from locust.runners import STATE_STOPPING, STATE_STOPPED

# gevent (Locust 내부에서 사용)
try:
    from gevent import spawn, sleep
except Exception:
    spawn = None

    def sleep(x):  # fallback (locust env면 보통 안 탐)
        time.sleep(x)

_BODY_INSTANCE_PATTERNS = [
    # app.js 응답에서 Host/Hostname 파싱 (예: "Hostname: ip-...")
    re.compile(r"(?:Host|Hostname)\s*:\s*(.*?)(?:<|\s|$)", re.IGNORECASE),
]
_HTTP_CODE_RE = re.compile(r"\b([1-5]\d{2})\b")

def extract_server_id_from_body(response):
    """OBSERVE 응답 body에서 Host/Hostname 문자열로 server id 추출."""
    if response is None:
        return None
    text = getattr(response, "text", None)
    if not text:
        return None
    for pat in _BODY_INSTANCE_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(1).strip()
    return None

def is_failure(exception, response):
    """request 실패 판정: exception or response None or status>=400."""
    if exception is not None:
        return True
    if response is None:
        return True
    status = getattr(response, "status_code", 0)
    return status >= 400

def _percentile(sorted_vals, q):
    """q in [0,1], ceil-index percentile (simple)."""
    n = len(sorted_vals)
    if n == 0:
        return None
    i = int(math.ceil(q * n)) - 1
    i = max(0, min(i, n - 1))
    return float(sorted_vals[i])

def _now_str(ts=None):
    ts = time.time() if ts is None else ts
    return time.strftime("%H:%M:%S", time.localtime(ts))


class MetricsTracker:
    """Locust 테스트 중 발생하는 모든 메트릭과 상태를 캡슐화하여 추적하는 클래스"""
    
    def __init__(self, config):
        self.config = config
        
        # Global Metrics & State Tracking
        self.instance_hits = defaultdict(int)
        self.server_first_seen = {}  # {server_id: timestamp}
        
        self.first_error_time = None
        self.last_error_time = None
        self.success_count = 0
        self.failure_count = 0
        self.current_outage_start = None
        self.outage_periods = []
        self.test_start_ts = None
        self.locust_env = None
        
        # Step-SLA window stats (OBSERVE만)
        self._step_obs_rts = []
        self._step_obs_succ = 0
        self._step_obs_fail = 0
        self._step_last_idx = 1
        self._step_monitor_g = None
        
        # Step-SLA stop 기록 (Summary 출력용)
        self.step_sla_stopped = False
        self.step_sla_stop_step = None
        self.step_sla_stop_p95 = None
        self.step_sla_stop_p99 = None
        self.step_sla_stop_fail_rate = None
        self.step_sla_stop_obs_total = None
        self.step_sla_stop_ts = None

        # Event Listeners 등록
        events.test_start.add_listener(self.reset_metrics)
        events.request.add_listener(self.collect_metrics)
        events.quitting.add_listener(self.print_summary)

    def _iter_error_rows(self):
        """Locust stats.errors -> (name, method, error_msg, occurrences)"""
        if self.locust_env is None:
            return

        errs = getattr(getattr(self.locust_env, "stats", None), "errors", None)
        if not errs:
            return

        for err_key, err_stats in errs.items():
            name = getattr(err_key, "name", "") or ""
            method = getattr(err_key, "method", "") or ""
            err_msg = getattr(err_key, "error", None)
            if err_msg is None:
                err_msg = str(err_key)

            occ = getattr(err_stats, "occurrences", None)
            if occ is None:
                occ = getattr(err_stats, "occurences", 0)

            yield name, method, str(err_msg), int(occ)

    def print_http_code_summary(self, only_name="OBSERVE"):
        """Summary에서 OBSERVE만 HTTP code별로 짧게 요약."""
        if self.locust_env is None:
            return

        counts = defaultdict(int)
        total = 0

        for name, method, err_msg, occ in self._iter_error_rows() or []:
            if name != only_name:
                continue
            m = _HTTP_CODE_RE.search(err_msg)
            if not m:
                continue
            code = m.group(1)
            counts[code] += occ
            total += occ

        if total <= 0:
            return

        ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
        s = ", ".join([f"{code}:{cnt}" for code, cnt in ordered])
        print(f"\n[HTTP Error Codes] (name={only_name}) {s} (total={total})")

    def print_top_failures_only(self, only_name="OBSERVE", top_n=None):
        """Locust 기본 Error report 대체가 아니라, Summary에서 OBSERVE 실패 이유 Top-N만 짧게 보여주기."""
        if top_n is None:
            top_n = self.config.TOP_N_FAILURES
            
        if self.locust_env is None:
            return

        rows = []
        for name, method, err_msg, occ in self._iter_error_rows() or []:
            if name != only_name:
                continue
            rows.append((occ, method, err_msg))

        if not rows:
            return

        rows.sort(key=lambda x: x[0], reverse=True)

        print(f"\n[Top Failure Reasons] (name={only_name})")
        for occ, method, err_msg in rows[:top_n]:
            print(f"  {occ:5d} | {method:4} | {err_msg}")

    def _step_sla_monitor(self):
        """
        Step-up 모드에서만 사용:
        - step 경계마다 직전 step의 OBSERVE p95/p99, fail% 출력
        - p95 > SLA_P95_MS 이면 runner.quit()
        """
        if self.locust_env is None:
            return

        t0 = self.test_start_ts or time.time()
        self._step_last_idx = 1

        while True:
            runner = getattr(self.locust_env, "runner", None)
            if runner is None:
                sleep(1)
                continue

            st = getattr(runner, "state", None)
            if st in (STATE_STOPPING, STATE_STOPPED):
                return

            rt = time.time() - t0
            cur_step = int(rt // self.config.STEP_TIME) + 1  # 1-based

            # step boundary 넘어가면 직전 step flush
            while cur_step > self._step_last_idx:
                total = self._step_obs_succ + self._step_obs_fail
                users = self._step_last_idx * self.config.STEP_USERS
                fail_rate = (self._step_obs_fail / total) * 100.0 if total > 0 else 0.0

                p95 = p99 = None
                if len(self._step_obs_rts) >= self.config.SLA_STEP_MIN_SAMPLES:
                    s = sorted(self._step_obs_rts)
                    p95 = _percentile(s, 0.95)
                    p99 = _percentile(s, 0.99)

                if p95 is not None:
                    print(
                        f"\n[STEP RESULT] step={self._step_last_idx} users~{users} (spawn_rate={self.config.SPAWN_RATE}) "
                        f"obs_total={total} fail%={fail_rate:.2f} p95={p95:.1f}ms p99={p99:.1f}ms"
                    )
                    if p95 > self.config.SLA_P95_MS:
                        self.step_sla_stopped = True
                        self.step_sla_stop_step = self._step_last_idx
                        self.step_sla_stop_p95 = p95
                        self.step_sla_stop_p99 = p99
                        self.step_sla_stop_fail_rate = fail_rate
                        self.step_sla_stop_obs_total = total
                        self.step_sla_stop_ts = time.time()

                        print(f"[SLA STOP] step={self._step_last_idx} P95={p95:.1f}ms > SLA({self.config.SLA_P95_MS}ms). Stopping...")
                        runner.quit()
                        return
                else:
                    print(
                        f"\n[STEP RESULT] step={self._step_last_idx} users~{users} (spawn_rate={self.config.SPAWN_RATE}) "
                        f"obs_total={total} fail%={fail_rate:.2f} (insufficient samples for p95/p99)"
                    )

                # reset window
                self._step_obs_rts.clear()
                self._step_obs_succ = 0
                self._step_obs_fail = 0
                self._step_last_idx += 1

            # 다음 step 경계까지 sleep (짧게 쪼개서 stop 반응성 유지)
            next_boundary = t0 + (self._step_last_idx * self.config.STEP_TIME)
            while True:
                st = getattr(runner, "state", None)
                if st in (STATE_STOPPING, STATE_STOPPED):
                    return
                remaining = next_boundary - time.time()
                if remaining <= 0:
                    break
                sleep(min(0.5, remaining))

    def reset_metrics(self, environment, **kwargs):
        self.locust_env = environment

        self.instance_hits.clear()
        self.server_first_seen = {}
        self.server_last_seen = {}

        self.first_error_time = None
        self.last_error_time = None
        self.success_count = 0
        self.failure_count = 0
        self.current_outage_start = None
        self.outage_periods = []
        self.test_start_ts = time.time()

        self._step_obs_rts = []
        self._step_obs_succ = 0
        self._step_obs_fail = 0
        self._step_last_idx = 1
        self._step_monitor_g = None

        self.step_sla_stopped = False
        self.step_sla_stop_step = None
        self.step_sla_stop_p95 = None
        self.step_sla_stop_p99 = None
        self.step_sla_stop_fail_rate = None
        self.step_sla_stop_obs_total = None
        self.step_sla_stop_ts = None

        # Step-SLA monitor (옵션)
        if self.config.USE_STEP_SHAPE and self.config.ENABLE_STEP_SLA_STOP:
            if spawn is None:
                print(f"[{_now_str()}] Step SLA stop requested, but gevent is unavailable (spawn=None).")
            else:
                self._step_monitor_g = spawn(self._step_sla_monitor)
                print(f"[{_now_str()}] Step SLA monitor enabled.")

    def collect_metrics(self, request_type, name, response_time, response_length, response, context, exception, **kwargs):
        """
        custom 지표는 OBSERVE만 집계 (=client view).
        - WORK, FAULT_* , PROBE 요청은 Locust 기본 통계/리포트에서 확인
        """
        if name != "OBSERVE":
            return

        now = time.time()

        if is_failure(exception, response):
            self.failure_count += 1
            self._step_obs_fail += 1

            if self.first_error_time is None:
                self.first_error_time = now
            self.last_error_time = now

            if self.current_outage_start is None:
                self.current_outage_start = now
            return

        self.success_count += 1
        self._step_obs_succ += 1
        self._step_obs_rts.append(float(response_time))  # ms

        if self.current_outage_start is not None:
            self.outage_periods.append(now - self.current_outage_start)
            self.current_outage_start = None

        server_id = extract_server_id_from_body(response)
        if server_id:
            self.instance_hits[server_id] += 1
            if server_id not in self.server_first_seen:
                self.server_first_seen[server_id] = now
            self.server_last_seen[server_id] = now

    
    # --- Summary 출력 영역 (목차 형태로 분리)

    def print_summary(self, environment, **kwargs):
        """이벤트 종료 시 호출되는 메인 Summary 출력 함수"""
        if self.locust_env is None:
            print("\n[WARN] locust_env is None.")
            return

        self._flush_outage_state()

        # 마치 책의 목차처럼 어떤 항목들이 출력되는지 한눈에 보입니다.
        self._print_config()
        self._print_stop_reason()
        self._print_load_balancing_and_scaling()
        self._print_reliability_metrics()
        self._print_service_outages()

        print("=" * 60 + "\n")

    def _flush_outage_state(self):
        now = time.time()
        if self.current_outage_start is not None:
            self.outage_periods.append(now - self.current_outage_start)
            self.current_outage_start = None

    def _print_config(self):
        fault_mode_label = "KILL ALL" if self.config.FAULT_MODE == "all" else "SINGLE"
        print("\n" + "=" * 60)
        print("INFRA PERFORMANCE SUMMARY")
        print("-" * 60)
        print("[Configuration]")
        print(f"  Target URL: {self.config.ALB_URL}")
        print(f"  Observe Path: {self.config.OBSERVE_PATH}")
        print(f"  Fault Injection: {'Enabled' if self.config.ENABLE_FAULT else 'Disabled'} (Mode: {fault_mode_label})")
        print(f"  Step SLA Stop: {'Enabled' if (self.config.USE_STEP_SHAPE and self.config.ENABLE_STEP_SLA_STOP) else 'Disabled'}")

    def _print_stop_reason(self):
        print("\n[Stop Reason]")
        if self.step_sla_stopped:
            elapsed = (self.step_sla_stop_ts - self.test_start_ts) if (self.step_sla_stop_ts and self.test_start_ts) else None
            elapsed_s = f"{elapsed:.1f}s" if elapsed is not None else "N/A"
            users = (self.step_sla_stop_step * self.config.STEP_USERS) if self.step_sla_stop_step else None
            users_s = f"{users}" if users is not None else "N/A"

            p95_s = f"{self.step_sla_stop_p95:.1f}ms" if self.step_sla_stop_p95 is not None else "N/A"
            p99_s = f"{self.step_sla_stop_p99:.1f}ms" if self.step_sla_stop_p99 is not None else "N/A"

            print("  Stopped early by STEP-SLA")
            print(f"    - step      : {self.step_sla_stop_step}")
            print(f"    - users~    : {users_s}")
            print(f"    - step_p95  : {p95_s}  (SLA={self.config.SLA_P95_MS}ms)")
            print(f"    - step_p99  : {p99_s}")
            print(f"    - fail%     : {self.step_sla_stop_fail_rate:.2f}")
            print(f"    - obs_total : {self.step_sla_stop_obs_total}")
            print(f"    - elapsed   : {elapsed_s}")
            print("  Note: Summary SLA below is computed over the entire test window.")
        else:
            print("  Normal stop (time_limit/manual/other).")

    def _print_load_balancing_and_scaling(self):
        print("\n[Load Balancing Status] (OBSERVE-based)")
        total_hits = sum(self.instance_hits.values())
        if total_hits == 0:
            print("  No host data collected via OBSERVE_PATH.")
            print("  Ensure the server response includes 'Host' or 'Hostname'.")
            return
        '''for server, count in sorted(self.instance_hits.items(), key=lambda x: x[1], reverse=True):
            ratio = (count / total_hits) * 100
            print(f"  {server:25} | Hits: {count:6} | Ratio: {ratio:5.1f}%")

        print(f"  Distinct Servers Detected: {len(self.instance_hits)}")

        if self.test_start_ts is not None:
            cutoff = self.test_start_ts + self.config.INITIAL_HOST_WINDOW_SEC
            initial_hosts = {h for h, ts in self.server_first_seen.items() if ts <= cutoff}
            new_hosts = [(h, ts) for h, ts in self.server_first_seen.items() if ts > cutoff]
            new_hosts.sort(key=lambda x: x[1])

            print("\n[Scaling Activity] (OBSERVE-based)")
            print(f"  Initial Hosts : {sorted(list(initial_hosts))}")
            if new_hosts:
                print("  New Hosts Detected (scale-out or replacement):")
                for h, ts in new_hosts:
                    dt = ts - self.test_start_ts
                    clock = time.strftime("%H:%M:%S", time.localtime(ts))
                    print(f"    + {h} (detected at {dt:.1f}s | {clock})")
            else:
                print("  No new hosts detected during the test.")'''
        for server, count in sorted(self.instance_hits.items(), key=lambda x: x[1], reverse=True):
            ratio = (count / total_hits) * 100
            print(f"  {server:25} | Hits: {count:6} | Ratio: {ratio:5.1f}%")

        print(f"  Distinct Servers Detected: {len(self.instance_hits)}")

        if self.test_start_ts is not None:
            cutoff = self.test_start_ts + self.config.INITIAL_HOST_WINDOW_SEC
            initial_hosts = [h for h, ts in self.server_first_seen.items() if ts <= cutoff]
            new_hosts = [(h, ts) for h, ts in self.server_first_seen.items() if ts > cutoff]
            new_hosts.sort(key=lambda x: x[1])

            print("\n[Scaling Activity]")
            print(f"  Initial Hosts : {sorted(initial_hosts)}")
            if new_hosts:
                print("  New Hosts Detected:")
                for h, ts in new_hosts:
                    dt = ts - self.test_start_ts
                    clock = time.strftime("%H:%M:%S", time.localtime(ts))
                    print(f"    + {h} (detected at {dt:.1f}s | {clock})")
            else:
                print("  No new hosts detected during the test.")

            print("\n[Server Lifespan]")
            current_time = time.time()
            for h in sorted(self.instance_hits.keys()):
                first_sec = self.server_first_seen[h] - self.test_start_ts
                last_sec = self.server_last_seen.get(h, self.server_first_seen[h]) - self.test_start_ts
                
                time_since_last_hit = current_time - self.server_last_seen.get(h, current_time)
                
                if time_since_last_hit > 60:
                    status = "TERMINATED"
                else:
                    status = "ALIVE"
                    
                print(f"    - {h:25} | {status:10} | First seen: {first_sec:6.1f}s ~ Last seen: {last_sec:6.1f}s")

    def _print_reliability_metrics(self):
        stats_total = self.locust_env.stats.total
        try:
            stats_observe = self.locust_env.stats.get("OBSERVE", "GET")
        except Exception:
            stats_observe = None

        print("\n[Reliability Metrics]")
        print(f"  Total Requests (ALL) : {stats_total.num_requests}")
        print(f"  Failed Requests(ALL) : {stats_total.num_failures}")

        print("\n  [Client View = OBSERVE]")
        total = self.success_count + self.failure_count
        print(f"    OBSERVE Requests  : {total}")
        print(f"    OBSERVE Failures  : {self.failure_count}")
        if self.first_error_time and self.last_error_time:
            print(f"    Error Duration    : {self.last_error_time - self.first_error_time:.1f}s")

        if stats_observe and stats_observe.num_requests > 0:
            p95 = stats_observe.get_response_time_percentile(0.95)
            p99 = stats_observe.get_response_time_percentile(0.99)
            print(f"    P95 Latency       : {p95:.1f} ms")
            print(f"    P99 Latency       : {p99:.1f} ms")
            sla_met = "YES" if p95 < self.config.SLA_P95_MS else "NO"
            print(f"    SLA Met (<{self.config.SLA_P95_MS}ms): {sla_met}")
        else:
            p95 = stats_total.get_response_time_percentile(0.95)
            p99 = stats_total.get_response_time_percentile(0.99)
            print(f"    P95 Latency (ALL fallback): {p95:.1f} ms")
            print(f"    P99 Latency (ALL fallback): {p99:.1f} ms")
            sla_met = "YES" if p95 < self.config.SLA_P95_MS else "NO"
            print(f"    SLA Met (<{self.config.SLA_P95_MS}ms): {sla_met}")

        if total > 0:
            print(f"    Success Rate      : {(self.success_count / total) * 100:.2f}%")

        self.print_http_code_summary(only_name="OBSERVE")
        self.print_top_failures_only(only_name="OBSERVE")

    def _print_service_outages(self):
        print("\n[Service Outages] (OBSERVE consecutive failures)")
        if not self.outage_periods:
            print("  No outage detected.")
        else:
            raw = list(self.outage_periods)
            raw_count = len(raw)
            raw_total = sum(raw)
            raw_max = max(raw) if raw else 0.0

            # 너무 짧은 outage는 summary에서 제외 (OUTAGE_MIN_SEC=0이면 전부)
            filt = [d for d in raw if d >= self.config.OUTAGE_MIN_SEC]
            filt_count = len(filt)

            if filt_count == 0:
                print(f"  Only micro-outages observed (<{self.config.OUTAGE_MIN_SEC:.3f}s).")
                print(f"    - raw_count : {raw_count}")
                print(f"    - raw_total : {raw_total:.3f}s")
                print(f"    - raw_max   : {raw_max:.3f}s")
            else:
                s = sorted(filt)
                p50o = _percentile(s, 0.50)
                p95o = _percentile(s, 0.95)
                p99o = _percentile(s, 0.99)
                top = sorted(filt, reverse=True)[:self.config.OUTAGE_TOP_N]

                print(f"  Outage summary (>= {self.config.OUTAGE_MIN_SEC:.3f}s)")
                print(f"    - count      : {filt_count}  (raw={raw_count})")
                print(f"    - total_time : {sum(filt):.3f}s  (raw_total={raw_total:.3f}s)")
                print(f"    - max        : {max(filt):.3f}s  (raw_max={raw_max:.3f}s)")
                print(f"    - p50/p95/p99: {p50o:.3f}s / {p95o:.3f}s / {p99o:.3f}s")

                print(f"  Top {len(top)} longest outages:")
                for i, d in enumerate(top, 1):
                    print(f"    #{i:02d} {d:.3f}s")
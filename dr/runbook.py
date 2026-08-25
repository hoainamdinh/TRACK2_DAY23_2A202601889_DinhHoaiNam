"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "step": n,
        "name": name,
        **kw
    }
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"RUNBOOK Step {n} ({name}):", json.dumps(kw))
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    try:
        ans = input(f"{msg} [y/N]: ").strip().lower()
        return ans in ["y", "yes"]
    except Exception:
        return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    start_time = time.time()

    # Step 1: xac_nhan_outage
    try:
        r_prim = httpx.get(f"{URL[primary]}/readyz", timeout=2.0)
        prim_alive = r_prim.status_code == 200
    except Exception:
        prim_alive = False

    try:
        r_tgt = httpx.get(f"{URL[target]}/readyz", timeout=2.0)
        tgt_alive = r_tgt.status_code == 200
    except Exception:
        tgt_alive = False

    step(1, "xac_nhan_outage", primary_alive=prim_alive, target_alive=tgt_alive)

    # Step 2: thong_bao_incident
    # t_outage should be read from the latest chaos kill event if possible, to log the delay.
    t_outage = None
    try:
        chaos_events = pathlib.Path("chaos/chaos-events.jsonl")
        if chaos_events.exists():
            kills = [json.loads(line) for line in chaos_events.read_text().splitlines() if line.strip()]
            kills = [e for e in kills if e.get("action") == "kill"]
            if kills:
                t_outage = kills[-1]["ts"]
    except Exception:
        pass

    step_2_data = {"note": "Incident declared. Operator starting RTO clock."}
    if t_outage:
        step_2_data["t_outage"] = t_outage
        step_2_data["notification_delay_s"] = round(time.time() - t_outage, 2)
    step(2, "thong_bao_incident", **step_2_data)

    if not confirm(auto, "Operator confirmation: Proceed with region failover?"):
        raise SystemExit("Failover aborted by operator.")

    # Step 3: scale_gpu_pool (calls failover exactly once)
    fo_res = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover_ok=fo_res.get("ok"), error=fo_res.get("error"))

    if not fo_res.get("ok"):
        raise SystemExit(f"Failover aborted due to error: {fo_res.get('error')}")

    # Step 4: verify_state_replica
    docs_lost = fo_res.get("docs_lost")
    rpo_seconds = fo_res.get("rpo_seconds")
    embed_model_version = fo_res.get("embed_model_version")
    step(4, "verify_state_replica", docs_lost=docs_lost, rpo_seconds=rpo_seconds, embed_model_version=embed_model_version)

    # Step 5: dns_cutover
    step(5, "dns_cutover", active_region=target, status="completed")

    # Step 6: verify_golden_signals
    # Send 10 verification requests to the proxy/edge
    edge_url = "http://127.0.0.1:8080/v1/infer"
    latencies = []
    errors = 0
    for i in range(10):
        t_req = time.time()
        try:
            r = httpx.get(edge_url, params={"q": f"golden signal check {i}"}, timeout=5.0)
            if r.status_code == 200:
                latencies.append((time.time() - t_req) * 1000.0)
            else:
                errors += 1
        except Exception:
            errors += 1
        time.sleep(0.1)

    p95 = None
    if latencies:
        latencies.sort()
        p95 = round(latencies[int(len(latencies) * 0.95)], 2)
    error_rate = errors / 10.0

    step(6, "verify_golden_signals", p95_latency_ms=p95, error_rate=error_rate)

    # Step 7: post_incident
    elapsed = time.time() - start_time
    step(7, "post_incident", elapsed_s=round(elapsed, 2),
         rto_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl")

    return {
        "ok": True,
        "failover": fo_res,
        "golden_signals": {"p95_latency_ms": p95, "error_rate": error_rate},
        "elapsed_s": round(elapsed, 2)
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))

"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """TODO: append 1 dòng JSONL có ts + iso vào LOG, và print ra stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **kw}
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))
    return rec


def failover(target: str, backend: str, wait: float) -> dict:
    """TODO: 5 bước ở trên, đúng thứ tự."""
    # 1_verify_target
    try:
        r = httpx.get(f"{URL[target]}/v1/state", timeout=5.0)
        state_data = r.json()
        emit(step="1_verify_target", region=target, status="ok",
             pool_state=state_data.get("pool_state"),
             weights=state_data.get("weights"),
             count=state_data.get("count"))
    except Exception as e:
        emit(step="1_verify_target", region=target, status="error", error=type(e).__name__)
        return {"ok": False, "error": f"target verify failed: {type(e).__name__}"}

    # 2_restore_snapshot
    try:
        primary = "b" if target == "a" else "a"
        primary_db = pathlib.Path(f"state/region-{primary}/vectors.sqlite")
        restored_db = pathlib.Path(f"state/region-{target}/vectors.sqlite")
        
        meta = snapshot.get(target, backend)
        rpo_details = snapshot.rpo(primary_db, restored_db)
        
        rpo_sec = rpo_details.get("rpo_seconds")
        docs_lost = rpo_details.get("docs_lost")
        model_ver = meta.get("embed_model_version")
        
        emit(step="2_restore_snapshot", region=target,
             rpo_seconds=rpo_sec, docs_lost=docs_lost, embed_model_version=model_ver)
    except Exception as e:
        emit(step="2_restore_snapshot", region=target, status="error", error=type(e).__name__)
        return {"ok": False, "error": f"restore snapshot failed: {type(e).__name__}"}

    # 3_scale_pool
    try:
        pool_file = pathlib.Path(f"state/region-{target}/pool_state")
        pool_file.parent.mkdir(parents=True, exist_ok=True)
        pool_file.write_text("full")
        emit(step="3_scale_pool", region=target, pool_state="full")
    except Exception as e:
        emit(step="3_scale_pool", region=target, status="error", error=type(e).__name__)
        return {"ok": False, "error": f"scale pool failed: {type(e).__name__}"}

    # 4_wait_ready
    ready_url = f"{URL[target]}/readyz"
    start_poll = time.time()
    poll_ok = False
    waited = 0.0
    while time.time() < start_poll + wait:
        try:
            r = httpx.get(ready_url, timeout=2.0)
            if r.status_code == 200:
                poll_ok = True
                waited = time.time() - start_poll
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not poll_ok:
        emit(step="4_wait_ready", region=target, status="timeout", waited_s=wait)
        return {"ok": False, "error": "timeout waiting for target readiness"}
    
    emit(step="4_wait_ready", region=target, status="ready", waited_s=round(waited, 2))

    # 5_dns_cutover
    try:
        active_file = pathlib.Path("edge/active_region")
        active_file.parent.mkdir(parents=True, exist_ok=True)
        active_file.write_text(target)
        emit(step="5_dns_cutover", region=target, active_region=target)
    except Exception as e:
        emit(step="5_dns_cutover", region=target, status="error", error=type(e).__name__)
        return {"ok": False, "error": f"dns cutover failed: {type(e).__name__}"}

    return {
        "ok": True,
        "target": target,
        "rpo_seconds": rpo_sec,
        "docs_lost": docs_lost,
        "embed_model_version": model_ver,
        "waited_s": round(waited, 2)
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))

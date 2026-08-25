# Runbook 1 trang — Region chính down

Runbook phải chạy được lúc 3h sáng bởi người KHÔNG viết nó. Mỗi bước: lệnh copy-paste được + cách biết bước đó xong.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python chaos/kill_region.py status` | `a.alive=false` 3 lần liên tiếp | on-call |
| 2 | Mở incident + bấm giờ RTO | `python dr/runbook.py --primary a --target b --backend fs --auto` | ts ghi vào `reports/runbook-run.jsonl` | on-call |
| 3 | Restore state ở region phụ | `python state/snapshot.py get --region b --backend fs` | Output manifest in ra meta snapshot thành công | on-call |
| 4 | Scale pool warm→full | `echo full > state/region-b/pool_state` | `/readyz` của b trả 200 | on-call |
| 5 | DNS/LB cutover | `echo b > edge/active_region` | `curl localhost:8080/edge/state` cho `active_region=b` | on-call |
| 6 | Verify golden signals | Gửi 10 request thử vào Edge (`localhost:8080/v1/infer`) | p95 < 600ms, error rate < 0.25 | on-call |
| 7 | Đo RTO + postmortem | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl` | `rto_verdict` != null | on-call |

**Rollback (failover ngược):** 
*   **Điều kiện rollback:** Chỉ trả traffic về Region A khi Region A hoạt động ổn định trở lại (`alive=true` và `ready=true` trên `/readyz`), các file weights được đồng bộ đầy đủ và sau khi đã sync ngược dữ liệu delta được ghi nhận tại Region B trong quá trình outage sang Region A.
*   **Người quyết định:** Trưởng bộ phận Hạ tầng (Head of Infrastructure) hoặc Chỉ huy Sự cố (Incident Commander) phê duyệt thủ công (không tự động rollback để tránh flapping).

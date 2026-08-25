# RTO/RPO Evidence — Lab 23

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về **một dòng log thật**
(`đường/dẫn.jsonl:số_dòng`). `pytest tests/test_rto_evidence.py` sẽ mở từng file ra kiểm tra.
Con số không có evidence = trượt, bất kể các phần khác.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | `2026-08-25T04:59:36` | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+0.2s` | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:16` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage | `reports/measure-drill-1.json` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0 | `action:kill` | `chaos/chaos-events.jsonl:5` |
| User thấy lỗi đầu tiên | `+2.1s` | dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:55` |
| Health check phát hiện | `+15.1s` | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:2` |
| Snapshot restore xong | `+19.6s` | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region phụ ready | `+25.9s` | `step:4_wait_ready` | `reports/failover-events.jsonl:4` |
| DNS cutover | `+25.9s` | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| **RTO đo được** | `+31.3s` | dòng `ok:true` đầu sau lỗi | `reports/drill-2-withdr.jsonl:68` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | `31.3s` | 300s (5 phút) | PASS |
| RPO — Vector DB | `0.0s` / `0` doc | 300s (5 phút) | PASS |

## 3. RTO của tôi gồm những gì (bắt buộc — đây là phần chấm điểm hiểu bài)

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | `15.0s` | `interval_s × threshold` trong `reports/health-events.jsonl:2` | Giảm `interval_s` hoặc `threshold` xuống (tuy nhiên tăng nguy cơ bị flapping). |
| Snapshot restore | `0.0s` | 2_restore → 3_scale trong `reports/failover-events.jsonl:2` | Tối ưu hóa cấu trúc dữ liệu SQLite hoặc dùng ổ cứng SSD/mạng truyền tải nhanh hơn. |
| GPU pool warm-up | `6.4s` | `waited_s` ở `4_wait_ready` trong `reports/failover-events.jsonl:4` | Sử dụng kỹ thuật preload weights hoặc tối ưu hóa GPU initialization time. |
| DNS/LB TTL cache | `5.4s` | t_recovered − t_cutover trong `reports/drill-2-withdr.jsonl:68` | Giảm `EDGE_TTL_SECONDS` tại edge proxy (tuy nhiên làm tăng tải DNS server). |

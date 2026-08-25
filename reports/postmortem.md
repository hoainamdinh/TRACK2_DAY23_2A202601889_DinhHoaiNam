# Postmortem — DR Drill Lab 23

Theo đúng template §4 "Sau Failover: Blameless Postmortem". Blameless: câu hỏi là "hệ thống/process nào cho phép chuyện này", không phải "ai làm sai".

## 1. Timeline (mọi dòng phải có evidence path:line)

| ISO time | Sự kiện | Evidence |
|---|---|---|
| `2026-08-25T05:08:59Z` | outage bắt đầu | `chaos/chaos-events.jsonl:5` |
| `2026-08-25T05:09:01Z` | user đầu tiên bị ảnh hưởng | `reports/drill-2-withdr.jsonl:55` |
| `2026-08-25T05:09:14Z` | health check alert | `reports/health-events.jsonl:2` |
| `2026-08-25T05:09:18Z` | operator confirm cutover | `reports/failover-events.jsonl:1` |
| `2026-08-25T05:09:30Z` | resolved (request đầu tiên OK từ region phụ) | `reports/drill-2-withdr.jsonl:68` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s · đo được: `31.3s` · gap: `-268.7s`
- RPO mục tiêu: 300s · đo được: `0.0s` (`0` doc bị mất) · gap: `-300.0s`
- **Bước tốn nhiều giây nhất:** `Health-check detect floor` (15.0 giây) — vì chúng ta cần chờ 3 lần probe lỗi liên tục (`interval_s × threshold = 5 × 3 = 15s`) để chắc chắn Region A thật sự gặp sự cố và tránh hiện tượng flapping.

## 3. Root cause (5 whys)

1. Tại sao user không thể thực hiện suy luận AI? -> Vì Region A bị netblock (outage) và không trả lời request.
2. Tại sao proxy không tự động chuyển vùng ngay lập tức? -> Vì Proxy cần chờ `active_region` trong file cấu hình được chuyển sang `b` và TTL cache (5 giây) hết hạn.
3. Tại sao active_region không được chuyển vùng ngay? -> Vì quy trình failover là bán tự động, yêu cầu chạy runbook và operator cần kiểm tra/chờ health check ghi nhận alert.
4. Tại sao health check ghi nhận chậm? -> Vì hệ thống được thiết lập ngưỡng chống flap `interval=5s` và `threshold=3` (tổng thời gian tối thiểu 15s).
5. Tại sao Region B cần tới 6.4s để phục vụ? -> Vì GPU pool của Region B khởi chạy ở trạng thái `warm` và cần thời gian nạp weights và khởi tạo CUDA context (mô phỏng GPU warm-up).

## 4. Action items (có owner + deadline)

| # | Action | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | Thực hiện pre-warm GPU/model weights tại Region B khi nhận tín hiệu cảnh báo đầu tiên | platform-team | 2026-09-01 | Giảm ~4s RTO |
| 2 | Tinh chỉnh TTL DNS (EDGE_TTL_SECONDS) từ 5s xuống còn 2s | traffic-team | 2026-09-01 | Giảm ~3s RTO |

## 5. Ba câu hỏi bắt buộc trả lời

1. `interval × threshold` của bạn là bao nhiêu giây? Nó chiếm bao nhiêu % RTO?
   -> `5s × 3 = 15s`. Nó chiếm `15.0 / 31.3 ≈ 47.9%` tổng thời gian RTO đo được.
2. Nếu hạ interval xuống 1s, RTO giảm mấy giây — và bạn trả giá gì (§4 flapping)?
   -> RTO sẽ giảm khoảng `12s` (do detection floor giảm từ 15s xuống còn 3s). Tuy nhiên, hệ thống sẽ nhạy cảm hơn với các lỗi mạng chập chờn tạm thời (network jitter), dẫn đến việc kích hoạt failover giả lập và gây ra hiện tượng flapping (chuyển vùng qua lại liên tục không cần thiết).
3. Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, `docs_lost` của bạn có nghĩa gì với khách hàng?
   -> `docs_lost` là số tài liệu của khách hàng bị mất hoàn toàn do không kịp replicate từ Region A sang Region B trước khi A sập. Trong trường hợp này là `0` tài liệu, điều đó có nghĩa là dữ liệu của khách hàng được bảo toàn trọn vẹn, không có giao dịch hay văn bản nào bị mất mát.

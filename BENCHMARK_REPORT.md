# Benchmark & Observability Report — Day 14

**Họ tên và MSSV** Nguyễn Quốc Hưng — 2A202603553

**Ngày chạy** 25/11/2026, GMT+7

**Môi trường** GitHub Actions Ubuntu x86_64, 4 vCPU Intel Xeon, 15 GiB RAM, Python 3.11.18. Các bài stack chạy trong Docker. [Lượt chạy benchmark](https://github.com/hung309911/K5-L3A-DAY14-NguyenQuocHung-2A202603553-Deployment-Benchmark-Observability/actions/runs/45645597090).

## 1. SLO của agent

| SLI | SLO | Lý do |
| --- | --- | --- |
| p95 latency `/ask` | ≤ 500 ms | Ngưỡng của load test nhằm giữ phần lớn request dưới nửa giây |
| Error rate `/ask` | ≤ 2% | Mức lỗi chấp nhận được cho bài test có lỗi upstream mô phỏng |
| Accuracy trên golden dataset | ≥ 100% ở cổng cuối | Bộ câu hỏi nhỏ, cố định và phải giữ các câu đã vượt qua ở baseline |

Các ngưỡng 500 ms và 2% dùng cho benchmark develop. Stack production dùng các alert riêng theo mẫu với p95 > 1 giây và error rate > 5%.

### 1.1. Đọc `/metrics` (Exercise 7.1)

Sau một request `POST /ask` thành công, bản develop xuất `http_requests_total{method="POST",route="/ask",status="200"} 1.0` (Counter), `agent_in_flight_requests 0.0` (Gauge) và họ `http_request_duration_seconds_bucket` (Histogram). Counter đếm tích lũy, Gauge tăng hoặc giảm theo request đang chạy và Histogram gom quan sát vào các bucket.

Middleware dùng route mẫu và gom URL không khớp vào `unmatched`. Nếu dùng URL thật, mỗi URL ngẫu nhiên có thể tạo thêm time series, làm tăng bộ nhớ, dung lượng lưu trữ và tải truy vấn Prometheus. Histogram `agent_question_length_chars` đo độ dài câu hỏi hợp lệ theo các ngưỡng từ 20 đến 2000 ký tự. Độ dài được đưa vào giá trị quan sát, nên mọi request dùng chung tập bucket cố định. Lượt kiểm tra sau khi thêm metric ghi `agent_question_length_chars_count 1.0` và `agent_question_length_chars_sum 32.0` cho một câu dài 32 ký tự.

## 2. Benchmark hiệu năng

### 2.1. Concurrency và latency

Mỗi mức chạy 300 request trên v2 gốc, `MOCK_SLOW_RATE=0.03` và `MOCK_ERROR_RATE=0.01`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 4.05 | 188.7 | 221.0 | 1698.1 | 1.00% | PASS |
| 10 | 44.70 | 187.5 | 217.6 | 1676.6 | 2.00% | PASS |
| 50 | 106.68 | 191.4 | 251.9 | 1715.8 | 1.67% | PASS |
| 100 | 59.45 | 321.6 | 3068.3 | 3294.3 | 1.00% | FAIL |

RPS tăng tới mức concurrency 50 rồi giảm ở 100, đồng thời cả median và phần đuôi tăng. Kết quả gợi ý mức 100 đã tạo áp lực lên server hoặc client đo tải chạy cùng runner. Cần profiling riêng để xác định điểm nghẽn. Ở mức 10, lỗi đúng 2% nên vẫn đạt điều kiện `≤ 2%` của script.

### 2.2. Tail latency

Mỗi hàng chạy 300 request với concurrency 20, giữ `MOCK_ERROR_RATE=0.01`.

| `MOCK_SLOW_RATE` | Avg (ms) | p95 (ms) | p99 (ms) |
| --- | ---: | ---: | ---: |
| 0 | 186.7 | 214.9 | 219.3 |
| 0.03 | 259.4 | 220.2 | 1707.3 |
| 0.1 | 371.1 | 1700.7 | 1715.9 |

Khi slow rate tăng từ 0 lên 3%, p99 nhảy từ 219.3 lên 1707.3 ms trong khi p95 chỉ tăng 5.3 ms. Ở 10%, p95 vượt 1.7 giây dù trung bình khoảng 371 ms. Vì vậy average có thể che phần request chịu độ trễ cao.

## 3. Benchmark chất lượng

Mọi lượt eval dùng cùng golden dataset và tắt lỗi upstream ngẫu nhiên bằng `MOCK_ERROR_RATE=0` để so sánh thay đổi knowledge base. Các load test ở mục 2 vẫn bật lỗi mô phỏng.

| Chỉ số | Agent v1 | Agent v2 | Agent v2 sau sửa KB |
| --- | ---: | ---: | ---: |
| Accuracy | 4.35% (1/23) | 91.30% (21/23) | 100% (23/23) |
| Avg tokens / câu | 56.9 | 109.0 | 114.6 |
| p50 latency eval (ms) | 170.5 | 183.1 | 182.1 |
| Tổng cost (USD) | 0.000401 | 0.000759 | 0.000798 |
| Cost / câu đúng (USD) | 0.00040095 | 0.00003616 | 0.00003472 |
| Quality gate | FAIL | PASS | PASS |

| ID | Keyword thiếu trong v2 gốc | Thay đổi |
| --- | --- | --- |
| `security-02` | token, hết hạn, chữ ký | Bổ sung tài liệu về JWT và API key |
| `scaling-03` | cpu, memory, custom metrics | Bổ sung tài liệu về tín hiệu Kubernetes HPA dùng để scale |

Retrieval gần gấp đôi lượng token trung bình từ v1 đến v2, nhưng số câu đúng tăng từ 1 lên 21 và chi phí trên mỗi câu đúng giảm khoảng 11 lần. Bản sửa đạt 23/23 với cost/câu đúng thấp hơn nữa. Retrieval có thêm một bước tìm tài liệu và prompt dài hơn, nên cần đọc latency cùng với accuracy và cost. Bộ eval nhỏ và có latency ngẫu nhiên, thuận lợi để phát hiện regression nội dung.

Job [Quality gate](.github/workflows/quality-gate.yml) bật v2 từ commit đang kiểm tra rồi chạy trong thư mục `develop/` bằng `python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline ../../../reports/eval_v2.json --out "$RUNNER_TEMP/eval_ci.json"` với report baseline đã commit. Job thất bại khi script trả exit code 1 do thiếu accuracy hoặc có regression. Đặt job làm required status check để chặn merge khi thất bại.

## 4. Observability stack

### 4.1. Grafana và Golden Signals

Chạy stack production với 2 replica, Locust 30 users và ramp-up 5 users/giây. Lúc chụp, dashboard hiển thị 16,0 req/s, error rate 0,59%, p95 290,48 ms, 3 request đang xử lý và 2 instance up. [Ảnh Grafana](screenshots/grafana-dashboard.png).

- Truy vấn Prometheus ở cửa sổ 1 phút ngay sau khi chụp ghi nhận p95 **291,23 ms** và average **225,20 ms**. Chênh lệch khoảng **66 ms**. Đường p99 trên dashboard ở gần 1,8 giây, cho thấy một nhóm nhỏ request chậm hơn rõ rệt.
- Hai replica xử lý khoảng **8,55** và **8,47** request `/ask` mỗi giây. Nginx chia gần đều ở lượt quan sát này.
- Input khoảng **1.326 token/s**, output khoảng **701 token/s**. Input gồm system prompt, câu hỏi và tài liệu retrieval.
- `GET http://localhost:8080/metrics` trả **404** do Nginx chặn ở cổng public. Prometheus scrape trực tiếp từng agent bên trong mạng Docker.
- `promtool test rules` báo `SUCCESS` trên bộ dữ liệu kiểm thử alert. [Lượt chạy Grafana và promtool](https://github.com/hung309911/K5-L3A-DAY14-NguyenQuocHung-2A202603553-Deployment-Benchmark-Observability/actions/runs/45646949317).

### 4.2. Thí nghiệm lỗi và cảnh báo

Các thời điểm firing được lấy từ Prometheus Alerts API. Thí nghiệm A và B chạy cùng lúc dưới tải 30 users để chụp hai cảnh báo đang firing trên cùng màn hình. [Ảnh Prometheus](screenshots/prometheus-alert-firing.png).

| # | Thay đổi | Alert quan sát | Thời gian tới firing | Panel liên quan |
| --- | --- | --- | ---: | --- |
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` | 78,3 s | Error rate |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` | 138,3 s | Latency p95 |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | khoảng 170,4 s | Cost burn rate |
| D | Pause một replica | `AgentInstanceDown` | 42,1 s | Instances up |
| E | `AGENT_VERSION=v1` | Không có alert firing | Theo dõi 120 s | Bảng version, retrieval hit ratio |

Khoảng từ thay đổi tới firing gồm thời gian áp dụng cấu hình, scrape, đánh giá biểu thức và thời gian `for`. Ở C, từ lúc bắt đầu tạo tải với cấu hình mới đến firing là 142,1 s. Tốc độ chi phí mô phỏng tại đó là 16,45 USD/giờ, vượt ngưỡng 2 USD/giờ. Ở E, `agent_info` xác nhận cả ba replica là v1. Hai lượt này dùng cửa sổ Prometheus mới để tránh dữ liệu từ pha trước. [Lượt đo C và E](https://github.com/hung309911/K5-L3A-DAY14-NguyenQuocHung-2A202603553-Deployment-Benchmark-Observability/actions/runs/45648769240).

Pause giữ container trong DNS để Prometheus vẫn scrape và thấy `up=0`. Nginx có thể trả timeout khi gọi replica bị pause. Lỗi do Nginx tạo cần đo thêm ở Nginx vì agent không ghi nhận request mà nó chưa xử lý.

Chất lượng suy giảm có thể được phát hiện bằng eval định kỳ, chạy cùng golden dataset cho phiên bản mới và baseline. Nên theo dõi accuracy theo nhóm câu hỏi và đặt ngưỡng regression trước khi rollout.

### 4.3. Debug bằng trace

Trace chậm `263f01f6658f66084acedf43f61199d6` kéo dài **1710.51 ms**. Span `chat mock-llm-small` mất **1693.50 ms** (khoảng 99.0% thời gian). Đây là bước chính trên đường xử lý, gồm thời gian chờ mock LLM. [Ảnh trace chậm](screenshots/jaeger-slow-trace.png).

```json
{"ts":"2026-11-25T15:47:38.264512+00:00","level":"INFO","service":"observable-agent","instance":"6e71a40caa02","event":"llm_call","trace_id":"263f01f6658f66084acedf43f61199d6","span_id":"7f755ac19ca84d94","request_id":"079601d3c947","model":"mock-llm-small","agent_version":"v2","input_tokens":76,"output_tokens":39,"cost_usd":3.5E-05,"llm_ms":1692.8}
```

Trace lỗi `b04e1af3ca15eaa76cd344e21da70edc` kết thúc với HTTP 503. Span model mất **72.69 ms** và có event `exception`. Log `llm_error` mang cùng `trace_id`. [Ảnh trace lỗi](screenshots/jaeger-error-trace.png).

```json
{"ts":"2026-11-25T15:47:33.542161+00:00","level":"INFO","service":"observable-agent","instance":"6c439d797263","event":"llm_error","trace_id":"b04e1af3ca15eaa76cd344e21da70edc","span_id":"4e7b3dcdab684166","request_id":"ca33955ca365","error":"Upstream LLM error: 503 Service Unavailable (mock)"}
```

## 5. Benchmark scaling bằng Locust

Locust headless chạy 100 users, ramp-up 10 users/giây và 2 phút mỗi lượt. Bảng lấy dòng `/ask` trong từng CSV.

| Replica | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 59,83 | 190 | 260 | 1700 | 65 / 7127 (0,91%) |
| 3 | 60,39 | 190 | 220 | 1700 | 72 / 7192 (1,00%) |

Khi scale lên 3 replica, p95 giảm 40 ms (khoảng 15,4%) trong lượt đo này. Throughput tăng khoảng 0,9%, p50 giữ ở 190 ms và p99 giữ ở 1,7 giây. Kịch bản Locust dành thời gian nghỉ giữa lượt gọi, còn delay của mock LLM nằm trên đường xử lý của từng request. Với tải này, các yếu tố đó giải thích vì sao thêm replica ít làm thay đổi RPS và p99. Cần lặp lại phép đo để tách ảnh hưởng của replica khỏi jitter và tỷ lệ request chậm ngẫu nhiên.

Prometheus xác nhận đúng 1 và 3 target agent trước và sau từng lượt. Lệnh chạy headless dùng thêm `--no-deps` sau `run --rm` để thao tác chạy Locust giữ nguyên số replica đang chạy. [Số liệu 1 replica](benchmark-results/scale_1_stats.csv) · [Số liệu 3 replica](benchmark-results/scale_3_stats.csv) · [Log scaling](https://github.com/hung309911/K5-L3A-DAY14-NguyenQuocHung-2A202603553-Deployment-Benchmark-Observability/actions/runs/45648599532).

## 6. Kết luận

1. p99 chỉ ra các request chậm mà average và p95 có thể bỏ qua ở tần suất thấp. Cần xem throughput, lỗi và nhiều percentile cùng nhau.
2. Chi phí trên câu đúng phản ánh tốt hơn giá trị của retrieval. Quality gate giúp phát hiện lỗi kiến thức mà metric hệ thống khó nhận ra.
3. Metrics định vị thời điểm xảy ra vấn đề. Trace chia độ trễ theo công đoạn và log cùng `trace_id` cung cấp chi tiết của request cụ thể.

Nếu đưa agent lên production, thêm SLI lỗi và latency ở Nginx, theo dõi `/ready` từ bên ngoài và chạy eval định kỳ trên tập câu hỏi cố định để bao phủ lỗi hạ tầng lẫn chất lượng.

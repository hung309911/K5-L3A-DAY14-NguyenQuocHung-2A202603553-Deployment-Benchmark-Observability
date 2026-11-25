# Triển khai agent Part 6

**Nền tảng chính** Railway Trial, với agent FastAPI và Redis trong project `k5-day14-agent`, môi trường `production`. [Dashboard Railway](https://railway.com/project/474a9d82-73ab-4ef2-9ea7-d6ec33c5e577).

**URL công khai** https://agent-production-8a157829.up.railway.app

## Cấu hình

Triển khai thư mục `06-lab-complete/` dùng Dockerfile và `railway.toml`. Health check của nền tảng gọi `/health`. Kiểm tra `/ready` để xác nhận agent kết nối được Redis.

| Biến | Thiết lập trên service agent (Railway production) |
| --- | --- |
| `AGENT_API_KEY` | Chuỗi ngẫu nhiên lưu trong Railway Variables |
| `REDIS_URL` | Tham chiếu URL riêng của Redis trong cùng project |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.00` |
| `PORT` | Dùng giá trị nền tảng cấp. Dockerfile mặc định 8000 |

## Triển khai từ repo

Từ thư mục gốc, đăng nhập Railway rồi tạo project và hai service trong cùng môi trường. Giữ `AGENT_API_KEY` trong phiên shell và gửi qua stdin.

```bash
railway login
railway init --name k5-day14-agent
railway add --database redis
railway add --service agent
AGENT_API_KEY=$(openssl rand -hex 32)
railway variable set --service agent --skip-deploys 'REDIS_URL=${{Redis.REDIS_URL}}' \
  ENVIRONMENT=production RATE_LIMIT_PER_MINUTE=10 DAILY_BUDGET_USD=1.00
printf '%s' "$AGENT_API_KEY" | railway variable set --service agent --skip-deploys --stdin AGENT_API_KEY
railway up ./06-lab-complete --path-as-root --service agent
railway domain --service agent
```

Dùng tên service Redis thực tế trong biến tham chiếu nếu tên khác `Redis`. Cờ `--path-as-root` giúp Railway dùng đúng Dockerfile và cấu hình Part 6. [Railway CLI](https://docs.railway.com/cli/up) · [Railway Redis](https://docs.railway.com/databases/redis).

## Lệnh xác minh CP6

Đặt `BASE` là URL public ở đầu trang và `AGENT_API_KEY` là key trong phiên shell. Kết quả thực đo ngày 25/11/2026 (GMT+7) là 200, 200, 401, 200.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'
```

| Kiểm tra trên Railway | HTTP |
| --- | ---: |
| `GET /health` | 200 |
| `GET /ready` | 200 |
| `POST /ask` thiếu API key | 401 |
| `POST /ask` với API key hợp lệ | 200 |

`/ready` trả `{"status":"ready","redis":"connected"}`. Bộ chấm gốc đã báo PASS 12/12 khi chạy với Docker trên Linux.

## Ảnh triển khai

- Dashboard Railway hiển thị môi trường production với 2/2 dịch vụ online. [Dashboard](screenshots/dashboard.png)
- Kết quả readiness trên URL công khai, Redis đã kết nối. [Agent running](screenshots/running.png)
- Bốn kết quả thực đo bằng curl và response tương ứng. API key đã được che. [Kiểm tra API](screenshots/test.png)

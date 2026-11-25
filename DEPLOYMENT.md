# Triển khai agent Part 6

**Nền tảng chính** Railway, với agent FastAPI và Redis trong cùng project.

**URL công khai** Chưa xác minh. Cần đăng nhập Railway để tạo dịch vụ và lấy URL.

## Cấu hình

Triển khai thư mục `06-lab-complete/` dùng Dockerfile và `railway.toml`. Health check của nền tảng gọi `/health`. Kiểm tra `/ready` để xác nhận agent kết nối được Redis.

| Biến | Thiết lập trên service agent |
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

Đặt `BASE` là URL public và `AGENT_API_KEY` là key trong phiên shell. Kết quả mong đợi theo thứ tự là 200, 200, 401, 200.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -d '{"question":"Hello"}'
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" \
  -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" \
  -d '{"question":"Hello"}'
```

Bộ chấm local đã chạy với Docker trên Linux và báo PASS 12/12. Mục triển khai thực tế chờ URL, kết quả bốn lệnh và ba ảnh `dashboard.png`, `running.png`, `test.png`.

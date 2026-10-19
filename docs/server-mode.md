# Chế độ server

Bật bằng `RIVIU_SERVER_MODE=1`. Khi tắt (mặc định) bản desktop và source chạy y như cũ.
Hướng dẫn triển khai (Docker, nginx, Let's Encrypt, đổi mật khẩu, cập nhật, sao lưu): `deploy/server/README.md`.

## Cấu hình (`riviu/server_mode.py`)

Thiếu `RIVIU_PUBLIC_ORIGIN`, bản băm mật khẩu hoặc `RIVIU_SECRET_KEY_FILE` thì ứng dụng **từ chối khởi động**.
`RIVIU_SECRET_KEY_FILE` phải chứa 32 byte base64, quyền 0600 (trên Linux) và nằm ngoài `RIVIU_DATA_DIR`.
Bản băm tạo bằng `python -m riviu.server_mode hash-password` (scrypt, salt ngẫu nhiên, so sánh thời gian hằng).

`python -m riviu` bind `RIVIU_HOST:RIVIU_PORT` (mặc định `127.0.0.1:1231`) và chỉ tin `X-Forwarded-For/Proto`
từ `RIVIU_FORWARDED_ALLOW_IPS` (mặc định `127.0.0.1`; trong Docker là gateway của mạng compose).

## Truy cập

- Host/Origin phải khớp `RIVIU_PUBLIC_ORIGIN`; Host loopback chỉ để kiểm tra sức khoẻ trên chính máy chủ.
  Fetch cross-site bị chặn như bản local.
- Không cần đăng nhập: `GET/POST /login`, `POST /logout`, `GET /auth-status`, `GET /healthz` (trả `ok`), và những gì trang đăng nhập tải (`/static/styles.css`, logo, favicon).
  `app.js`, icon và ảnh hướng dẫn (`/static/guide/`) cần đăng nhập.
  Mọi route khác và `/ws` cần cookie `__Host-riviu_auth` (HttpOnly, Secure, SameSite=Strict, mặc định 7 ngày).
  Thiếu cookie: trang HTML chuyển về `/login`, API trả 401, WebSocket bị đóng.
- Phiên là token ngẫu nhiên; server chỉ lưu SHA-256 (`data/server_sessions.json`) nên khởi động lại không mất đăng nhập.
  Đăng xuất thu hồi token; đổi mật khẩu làm mọi phiên cũ hết hiệu lực.
- Sai mật khẩu 5 lần trong 15 phút từ một IP: trả 429 tới khi hết cửa sổ, kể cả khi nhập đúng.
- `/_desktop/*` không tồn tại trên server (404); sidecar desktop từ chối chạy khi bật chế độ server.

## Khác với desktop

- **Cookie Threads**: không có DPAPI/keyring trên server headless, nên lưu ở `data/threads_session.aesgcm`,
  mã hoá AES-256-GCM bằng khoá `RIVIU_SECRET_KEY_FILE` (thư mục dữ liệu gắn vào dữ liệu liên kết). Không bao giờ ghi bản rõ,
  không ghi cookie vào log.
- **Đăng nhập Google**: server không mở được trình duyệt. `/google-oauth-login` trả URL đồng ý của Google
  (redirect `http://localhost:<cổng ngẫu nhiên>/`, PKCE S256). Người dùng duyệt trên máy mình, dán lại địa chỉ
  localhost (không tải được) vào hộp thoại; `/google-oauth-complete` kiểm tra `state`, đổi mã bằng code verifier
  giữ trên server và lưu token như bản desktop. Phiên chờ hết hạn sau 10 phút và chỉ dùng được một lần.
- Mọi người dùng chung dữ liệu và chung quy tắc một phiên quét mỗi lúc.

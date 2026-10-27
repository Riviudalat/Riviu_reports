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

## Tài khoản và vai trò (`riviu/server_users.py`)

- Mỗi người một tài khoản trong `data/server_users.json` (băm scrypt, không lưu mật khẩu). Hai vai trò:
  - `admin`: toàn quyền (quét, đổi nguồn, đồng bộ/đẩy Google, proxy, cookie, cài đặt).
  - `viewer` (nhân viên): chỉ xem sheet, danh sách đối tác, lịch sử và tải báo cáo đối tác
    (`security.VIEWER_ROUTES`). Mọi route khác trả 403; `/ws` vẫn xem được tiến trình nhưng lệnh `start`/`cancel` bị từ chối.
- Khi **chưa có tài khoản nào**, mật khẩu chung (`RIVIU_PASSWORD_HASH`) vẫn đăng nhập với quyền admin.
  Tạo tài khoản đầu tiên là mật khẩu chung hết tác dụng và các phiên đăng nhập bằng nó bị huỷ; từ đó phải nhập tên đăng nhập.
- Đổi mật khẩu hoặc xoá tài khoản thì các phiên của người đó hết hiệu lực; đổi vai trò có hiệu lực ngay. Luôn phải còn ít nhất một admin.
- `GET /auth-status` trả `{"authenticated": true, "user": {"username": "...", "role": "admin|viewer"}}` để giao diện ẩn/hiện chức năng.
- Quản lý (trên server: `docker compose -f deploy/server/compose.yaml exec riviu python -m riviu.server_mode ...`):

  ```
  python -m riviu.server_mode add-user <tên> admin|viewer   # mật khẩu đọc từ stdin hoặc hỏi ẩn
  python -m riviu.server_mode set-password <tên>
  python -m riviu.server_mode set-role <tên> admin|viewer
  python -m riviu.server_mode remove-user <tên>
  python -m riviu.server_mode list-users
  ```
- `/_desktop/*` không tồn tại trên server (404); sidecar desktop từ chối chạy khi bật chế độ server.

## Khác với desktop

- **Cookie Threads**: không có DPAPI/keyring trên server headless, nên lưu ở `data/threads_session.aesgcm`,
  mã hoá AES-256-GCM bằng khoá `RIVIU_SECRET_KEY_FILE` (thư mục dữ liệu gắn vào dữ liệu liên kết). Không bao giờ ghi bản rõ,
  không ghi cookie vào log.
- **Cookie TikTok** (tuỳ chọn, mặc định tắt): lưu ở `data/tiktok_session.aesgcm`, cùng khoá và cùng quy tắc
  như cookie Threads (bản desktop dùng DPAPI/keyring). Chỉ gửi kèm khi bật công tắc **Cookie TikTok** cho phiên quét.
- **Tài khoản dịch vụ Google** (khuyên dùng trên server): `data/google_service_account.json` được mã hoá
  AES-256-GCM bằng khoá `RIVIU_SECRET_KEY_FILE`; khi có, nó được dùng thay cho đăng nhập Google.
  Cách tạo: `docs/google-service-account.md`.
- **Đăng nhập Google**: server không mở được trình duyệt. `/google-oauth-login` trả URL đồng ý của Google
  (redirect `http://localhost:<cổng ngẫu nhiên>/`, PKCE S256). Người dùng duyệt trên máy mình, dán lại địa chỉ
  localhost (không tải được) vào hộp thoại; `/google-oauth-complete` kiểm tra `state`, đổi mã bằng code verifier
  giữ trên server và lưu token như bản desktop. Phiên chờ hết hạn sau 10 phút và chỉ dùng được một lần.
- Mọi người dùng chung dữ liệu và chung quy tắc một phiên quét mỗi lúc.

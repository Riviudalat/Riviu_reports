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
- **Trang xem công khai `/`** (không cần đăng nhập, `data-role="viewer"`): chọn nền tảng/file/sheet để xem dữ liệu,
  Tổng kết, lịch sử quét, mở **Đối tác & báo cáo** và xuất báo cáo. Chủ dự án đã chấp nhận dữ liệu này là công khai.
  Trang không render nút quản trị (quét/hủy, cấu hình quét, proxy, cookie, nạp file, Google, Nạp sheet, Tạo sheet,
  Cập nhật đối tác, Tải sheet, Đăng xuất), chỉ có nút **Quản trị** tới `/admin`. Lựa chọn file của người xem chỉ nằm
  trong `sessionStorage` của tab đó, không gửi lên server.
- **Danh sách cho phép không cần đăng nhập** (`security.viewer_request_allowed`, server mới là chốt chặn):
  `GET /`, `/static/*`, logo/favicon, `/api/version`, `/list-files`, `/preview-excel`, `/summary-dashboard`,
  `GET /source-preferences`, `/report-partners`, `/scrape-history`, `POST /export-report`, cùng `GET/POST /login`,
  `POST /logout`, `GET /auth-status` (trả `{authenticated, role}`) và `GET /healthz` (trả `ok`).
  `/ws` mở cho người xem nhưng chỉ đọc: lệnh `start`/`cancel` cần phiên quản trị, kiểm tra ở `riviu/api/scan.py` cho từng lệnh
  (middleware HTTP không chạy cho websocket). Với người xem, `/list-files` không đọc trạng thái Google.
- **Trang quản trị `/admin`** và mọi route khác cần cookie `__Host-riviu_auth` (HttpOnly, Secure, SameSite=Strict, mặc định 7 ngày).
  Thiếu cookie: `/admin` chuyển tới `/login?next=/admin` (đăng nhập xong quay lại `/admin`; `next` chỉ nhận `/admin` hoặc `/`),
  trang HTML khác chuyển về `/login`, API trả 401. Đăng xuất quay về `/`.
- Phiên là token ngẫu nhiên; server chỉ lưu SHA-256 (`data/server_sessions.json`) nên khởi động lại không mất đăng nhập.
  Đăng xuất thu hồi token; đổi mật khẩu làm mọi phiên cũ hết hiệu lực.
- Sai mật khẩu 5 lần trong 15 phút từ một IP: trả 429 tới khi hết cửa sổ, kể cả khi nhập đúng.
- `/_desktop/*` không tồn tại trên server (404); sidecar desktop từ chối chạy khi bật chế độ server.

## Lịch tự động (`riviu/scheduler.py`)

Chỉ chạy ở chế độ server (task asyncio khởi động cùng app). Quản trị viên mở **Lịch tự động** trên `/admin`
để thêm/xoá/bật/tắt khung giờ `HH:MM` (giờ Asia/Ho_Chi_Minh, UTC+7) cho từng nền tảng và chọn tuỳ chọn quét
(luồng, chế độ, proxy = danh sách proxy đã lưu, cookie). API: `GET/POST /schedule` (chỉ quản trị).

- Đến giờ, với nền tảng đó: (1) **Nạp sheet** từ link Google Sheet đã lưu của nền tảng (`data/source_preferences.json`,
  hoặc link đăng ký của file hiện tại) bằng đúng code của `/sync-google-sheet`, rồi đặt file mới làm nguồn của nền tảng
  (giữ sheet quét đã lưu nếu file mới còn sheet đó, không thì sheet dữ liệu đầu tiên); (2) quét file đó qua
  `prepare_scan_start` / `start_scan_task`, nên trang đang mở thấy tiến độ trực tiếp; (3) **không** tạo sheet trên Google.
- Từ lúc nạp tới khi phiên quét bắt đầu, lịch giữ `scan_state.scan_starting()`: quét tay và thao tác nguồn phải chờ.
- Bỏ qua (và ghi lại) khi đang có phiên quét hoặc thao tác nguồn, hay khi nền tảng chưa có link. Nạp lỗi
  (ví dụ cần đăng nhập Google) thì ghi lỗi và không quét. Hai nền tảng cùng giờ chạy lần lượt, không bao giờ song song.
- Vòng lặp thức dậy mỗi phút. Giờ bị lỡ khi server tắt (hoặc vòng lặp kẹt quá 3 phút) bị bỏ qua, không chạy bù.
- Quy tắc Threads giữ nguyên (giãn nhịp, dừng khi HTTP 429 và ghi là "Dừng sớm", không xoay proxy né 403/429).
- `data/scan_schedule.json` (cấu hình):
  `{"version": 1, "platforms": {"tiktok": {"slots": [{"time": "08:00", "enabled": true}], "options": {"workers": 10, "scrapeMode": "request", "useProxy": false, "useSession": false}}, "threads": {...}}}`.
  `data/scan_schedule_runs.json`: 50 kết quả gần nhất (`status`: completed, failed, skipped, stopped, cancelled).
  Cả hai ghi nguyên tử bằng `write_json_atomic`.

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

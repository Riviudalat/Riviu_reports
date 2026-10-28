# Chế độ server

Bật bằng `RIVIU_SERVER_MODE=1`. Khi tắt (mặc định) app chạy ở chế độ source (`python -m riviu` trên máy, chỉ nghe loopback) y như cũ.
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
- **Trang xem công khai `/`** (không cần đăng nhập, `data-role="viewer"`): trang báo cáo riêng
  (`templates/viewer_body.html`, CSS `.vw-*`, các hàm `renderViewer*` trong app.js). Mỗi nền tảng hiện đúng file
  nguồn của nó trong `/source-preferences` (file quản trị đã chọn hoặc lịch tự động vừa nạp), người xem không đổi
  file; chưa có nguồn thì hiện "Chưa có file báo cáo cho nền tảng này". Người xem chọn sheet trong file (các tháng
  và tab Tổng kết), lọc bảng link, xem lịch sử quét, và bấm **Xuất báo cáo** để mở hộp thoại Đối tác & báo cáo như
  trang quản trị. Không có tab trạng thái quét, thanh tiến độ hay nhật ký. Chủ dự án đã chấp nhận dữ liệu này là
  công khai. Trang không render nút quản trị (quét/hủy, cấu hình quét, proxy, cookie, nạp file, Google, Nạp sheet,
  Tạo sheet, Cập nhật đối tác, Tải sheet, Đăng xuất), chỉ có nút **Quản trị** tới `/admin`. Lựa chọn sheet của người
  xem không gửi lên server.
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

## Tài khoản và vai trò (`riviu/server_users.py`)

- Mỗi người một tài khoản trong `data/server_users.json` (băm scrypt, không lưu mật khẩu). Hai vai trò:
  - `admin`: toàn quyền (quét, đổi nguồn, đồng bộ/đẩy Google, proxy, cookie, cài đặt).
  - `viewer` (nhân viên): thấy đúng như khách không đăng nhập ở `/` (xem sheet, đối tác, lịch sử, tải báo cáo đối tác;
    `security.viewer_request_allowed`). Mọi route khác trả 403; `/ws` vẫn xem được tiến trình nhưng lệnh `start`/`cancel`/`skip_retry` bị từ chối.
- Khi **chưa có tài khoản nào**, mật khẩu chung (`RIVIU_PASSWORD_HASH`) vẫn đăng nhập với quyền admin.
  Tạo tài khoản đầu tiên là mật khẩu chung hết tác dụng và các phiên đăng nhập bằng nó bị huỷ; từ đó phải nhập tên đăng nhập.
- Đổi mật khẩu hoặc xoá tài khoản thì các phiên của người đó hết hiệu lực; đổi vai trò có hiệu lực ngay. Luôn phải còn ít nhất một admin.
- `GET /auth-status` trả `{"authenticated": true, "role": "admin|viewer", "user": {"username": "...", "role": "admin|viewer"}}` để giao diện ẩn/hiện chức năng.
- Quản lý (trên server: `docker compose -f deploy/server/compose.yaml exec riviu python -m riviu.server_mode ...`):

  ```
  python -m riviu.server_mode add-user <tên> admin|viewer   # mật khẩu đọc từ stdin hoặc hỏi ẩn
  python -m riviu.server_mode set-password <tên>
  python -m riviu.server_mode set-role <tên> admin|viewer
  python -m riviu.server_mode remove-user <tên>
  python -m riviu.server_mode list-users
  ```

## Lịch tự động (`riviu/scheduler.py`)

Chỉ chạy ở chế độ server (task asyncio khởi động cùng app). Quản trị viên mở **Lịch tự động** trên `/admin`
để thêm/xoá/bật/tắt khung giờ `HH:MM` (giờ Asia/Ho_Chi_Minh, UTC+7) cho từng nền tảng và chọn tuỳ chọn quét
(luồng, chế độ, proxy = danh sách proxy đã lưu, cookie). API: `GET/POST /schedule` (chỉ quản trị).

- Đến giờ, với nền tảng đó: (1) **Nạp sheet** từ link Google Sheet đã lưu của nền tảng (`data/source_preferences.json`,
  hoặc link đăng ký của file hiện tại) bằng đúng code của `/sync-google-sheet`, rồi đặt file mới làm nguồn của nền tảng
  trên sheet tháng mới nhất (`keep_or_newest_month_sheet`: giữ sheet quét đã lưu chỉ khi file mới còn sheet đó và không có
  tháng mới hơn); (2) quét file đó qua
  `prepare_scan_start` / `start_scan_task`, nên trang đang mở thấy tiến độ trực tiếp; (3) **không** tạo sheet trên Google.
- Từ lúc nạp tới khi phiên quét bắt đầu, lịch giữ `scan_state.scan_starting()`: quét tay và thao tác nguồn phải chờ.
- Bỏ qua (và ghi lại) khi đang có phiên quét hoặc thao tác nguồn, hay khi nền tảng chưa có link. Nạp lỗi
  (ví dụ cần đăng nhập Google) thì ghi lỗi và không quét. Hai nền tảng cùng giờ chạy lần lượt, không bao giờ song song.
- Vòng lặp thức dậy mỗi phút. Giờ bị lỡ khi server tắt (hoặc vòng lặp kẹt quá 3 phút) bị bỏ qua, không chạy bù.
- Quy tắc Threads giữ nguyên (giãn nhịp, cách ly tuyến proxy nhận HTTP 429, dừng và ghi là "Dừng sớm" khi mọi tuyến đều 429, không xoay proxy né 403/geoblock).
- Tùy chọn **Quét lại link lỗi** (`retryFailed`, mặc định bật) chạy một lượt quét lại các link lỗi tạm thời sau khi chờ; xem `docs/threads-scanning.md`.
- Phiên quét dở (lịch hoặc tay) bị gián đoạn do crash/restart container được tự tiếp tục khi app khởi động (trước vòng lặp lịch, nên khung giờ trùng lúc bị bỏ qua vì bận); lượt lịch được tiếp tục vẫn không đẩy Google. Xem `riviu/scan_journal.py`.
- **Sheet tháng mới nhất** (`riviu/workbook/naming.py` `default_data_sheet`): sau **Nạp sheet** (tay hoặc lịch), khi tải
  danh sách sheet và khi sheet đã lưu không còn, nguồn mở sheet dữ liệu của tháng mới nhất ("Tháng 10", "tháng 10", "Thg 10",
  "T10", "Tháng 10/2026"... không phân biệt hoa thường/dấu; không có năm thì là lần gần nhất của tháng đó tính tới hôm nay,
  nên tháng 1 mới hơn tháng 12 năm trước), bỏ qua tab "Tổng kết…" và tab kết quả theo giờ. Không nhận ra tháng nào thì
  dùng sheet dữ liệu đầu tiên như trước.
- **Cấu hình quét** của thanh công cụ (luồng, chế độ, Proxy xoay bật/tắt, Cookie Threads / Cookie TikTok bật/tắt) lưu theo
  từng nền tảng ở `data/scan_settings.json` (`GET/POST /scan-settings`, chỉ quản trị; chỉ cờ, không bao giờ lưu danh sách
  proxy hay cookie), nên đổi tab hay mở lại trang ở máy khác vẫn giữ nguyên. Lịch tự động chưa có file cấu hình thì lấy
  tuỳ chọn ban đầu từ đây.
- `data/scan_schedule.json` (cấu hình):
  `{"version": 1, "platforms": {"tiktok": {"slots": [{"time": "08:00", "enabled": true}], "options": {"workers": 10, "scrapeMode": "request", "useProxy": false, "useSession": false, "retryFailed": true}}, "threads": {...}}}`.
  `data/scan_schedule_runs.json`: 50 kết quả gần nhất (`status`: completed, failed, skipped, stopped, cancelled).
  Cả hai ghi nguyên tử bằng `write_json_atomic`.

## Khác với chế độ source

- **Cookie Threads**: không có DPAPI/keyring trên server headless, nên lưu ở `data/threads_session.aesgcm`,
  mã hoá AES-256-GCM bằng khoá `RIVIU_SECRET_KEY_FILE` (thư mục dữ liệu gắn vào dữ liệu liên kết). Không bao giờ ghi bản rõ,
  không ghi cookie vào log.
- **Cookie TikTok** (tuỳ chọn, mặc định tắt): lưu ở `data/tiktok_session.aesgcm`, cùng khoá và cùng quy tắc
  như cookie Threads (chế độ source dùng DPAPI/keyring). Chỉ gửi kèm khi bật công tắc **Cookie TikTok** cho phiên quét.
- **Tài khoản dịch vụ Google** (khuyên dùng trên server): `data/google_service_account.json` được mã hoá
  AES-256-GCM bằng khoá `RIVIU_SECRET_KEY_FILE`; khi có, nó được dùng thay cho đăng nhập Google.
  Cách tạo: `docs/google-service-account.md`.
- **Đăng nhập Google**: server không mở được trình duyệt. `/google-oauth-login` trả URL đồng ý của Google
  (redirect `http://localhost:<cổng ngẫu nhiên>/`, PKCE S256). Người dùng duyệt trên máy mình, dán lại địa chỉ
  localhost (không tải được) vào hộp thoại; `/google-oauth-complete` kiểm tra `state`, đổi mã bằng code verifier
  giữ trên server và lưu token như chế độ source. Phiên chờ hết hạn sau 10 phút và chỉ dùng được một lần.
- Mọi người dùng chung dữ liệu và chung quy tắc một phiên quét mỗi lúc.

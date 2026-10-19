# Riviu Reports trên server (chế độ server)

Chạy cùng ứng dụng trên một máy Linux cho cả nhóm, sau nginx với HTTPS, bảo vệ bằng **một mật khẩu chung**.
Mọi người dùng chung dữ liệu; mỗi lúc chỉ chạy được một phiên quét (như bản desktop).

- Ứng dụng chạy trong Docker, chỉ nghe `127.0.0.1:18110`. Cổng công khai duy nhất là nginx (80 → 443).
- Dữ liệu: `/srv/riviu/data` (workbook, OAuth Google, proxy, lịch sử, cookie Threads đã mã hoá, phiên đăng nhập).
- Bí mật (không bao giờ nằm trong git): `/etc/riviu/password.hash`, `/etc/riviu/secret.key`, `/etc/riviu/riviu.env`.
- Container chạy bằng user uid/gid `10001`, nên các file trên phải thuộc `10001:10001`.

## Cài lần đầu

```bash
# 1. Mã nguồn
sudo git clone https://github.com/Riviudalat/Riviu_reports.git /opt/riviu-reports
cd /opt/riviu-reports

# 2. Thư mục dữ liệu và bí mật
sudo install -d -m 750 -o 10001 -g 10001 /srv/riviu/data
sudo install -d -m 750 -o root -g 10001 /etc/riviu
sudo install -m 600 -o 10001 -g 10001 deploy/server/riviu.env.example /etc/riviu/riviu.env
sudo nano /etc/riviu/riviu.env        # kiểm tra RIVIU_PUBLIC_ORIGIN

# 3. Khoá mã hoá cookie Threads (32 byte, chỉ chủ sở hữu đọc được, nằm NGOÀI thư mục dữ liệu)
openssl rand -base64 32 | sudo tee /etc/riviu/secret.key >/dev/null
sudo chown 10001:10001 /etc/riviu/secret.key && sudo chmod 600 /etc/riviu/secret.key

# 4. Build image
sudo docker compose -f deploy/server/compose.yaml build

# 5. Mật khẩu chung -> chỉ lưu bản băm scrypt
read -rs -p "Mật khẩu chung: " PW; echo
printf '%s\n' "$PW" | sudo docker run --rm -i riviu-reports:latest python -m riviu.server_mode hash-password \
  | sudo tee /etc/riviu/password.hash >/dev/null; unset PW
sudo chown 10001:10001 /etc/riviu/password.hash && sudo chmod 600 /etc/riviu/password.hash

# 6. Chạy
sudo docker compose -f deploy/server/compose.yaml up -d
curl -fsS http://127.0.0.1:18110/healthz    # in ra: ok
```

Phải tạo các file ở bước 2, 3, 5 **trước** `up`: nếu thiếu, Docker sẽ tự tạo thư mục trùng tên và ứng dụng
từ chối khởi động (thiếu cấu hình in ra rõ trong `docker compose logs riviu`).

### nginx + Let's Encrypt

```bash
sudo install -d /var/www/letsencrypt
sudo cp deploy/server/nginx-riviu.conf /etc/nginx/sites-available/riviu
sudo ln -s /etc/nginx/sites-available/riviu /etc/nginx/sites-enabled/riviu
```

Lần đầu chưa có chứng chỉ: tạm comment khối `server { listen 192.168.2.5:443 ssl; ... }`, rồi

```bash
sudo nginx -t && sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/letsencrypt -d 113-161-254-76.sslip.io \
  --deploy-hook "systemctl reload nginx"
```

Bỏ comment khối 443, `sudo nginx -t && sudo systemctl reload nginx`. Gia hạn tự động qua timer của certbot.

Khối 443 dùng `listen 192.168.2.5:443 ssl;` (IP LAN) giống các site khác trên máy này, vì Tailscale đã giữ
các bind kiểu `:443`; đừng đổi thành `listen 443`.

### Trong ứng dụng

1. Mở https://113-161-254-76.sslip.io, nhập mật khẩu chung.
2. **Nguồn dữ liệu → Nạp OAuth**: tải lên file OAuth client Google (loại *Desktop app*).
3. **Đăng nhập Google**: bấm link trong hộp thoại, cho phép quyền. Trình duyệt sẽ chuyển tới
   `http://localhost:…/?state=…&code=…` và báo không tải được trang: sao chép **toàn bộ địa chỉ** đó dán vào
   hộp thoại rồi bấm *Xác nhận*. Server kiểm tra `state` và đổi mã bằng PKCE, lưu token như bản desktop.
4. Cookie Threads (nếu dùng): nhập như bản desktop; trên server được mã hoá AES-GCM bằng `secret.key`.

## Đổi mật khẩu

Làm lại bước 5 rồi `sudo docker compose -f deploy/server/compose.yaml restart`.
Mọi phiên đăng nhập cũ hết hiệu lực ngay (phiên gắn với bản băm mật khẩu).

Sai mật khẩu 5 lần trong 15 phút từ cùng một IP sẽ bị khoá tới khi hết 15 phút đó.

## Cập nhật

```bash
cd /opt/riviu-reports && sudo deploy/server/update.sh
```

Script `git pull --ff-only`, build lại image, `up -d`, rồi chờ `/healthz`; nếu lỗi in log gần nhất.
Không cập nhật khi đang có người quét: container khởi động lại sẽ dừng phiên quét đó.

## Sao lưu / khôi phục

```bash
sudo tar -C / -czf riviu-backup-$(date +%F).tgz srv/riviu/data etc/riviu
```

`secret.key` cần đi cùng dữ liệu: thiếu nó thì không giải mã được cookie Threads (chỉ cần nhập lại cookie).
Giữ file sao lưu ở nơi riêng tư vì có token Google và khoá. Khôi phục: dừng container, giải nén vào `/`,
`chown -R 10001:10001 /srv/riviu/data`, rồi `up -d`.

## Biến môi trường

| Biến | Ý nghĩa |
| --- | --- |
| `RIVIU_SERVER_MODE=1` | Bật chế độ server (mặc định tắt; desktop/source không đổi) |
| `RIVIU_PUBLIC_ORIGIN` | Địa chỉ công khai, Host/Origin phải khớp |
| `RIVIU_PASSWORD_HASH` / `RIVIU_PASSWORD_HASH_FILE` | Bản băm scrypt của mật khẩu chung |
| `RIVIU_SECRET_KEY_FILE` | Khoá 32 byte base64, quyền 0600, ngoài thư mục dữ liệu |
| `RIVIU_HOST`, `RIVIU_PORT` | Địa chỉ bind (container: `0.0.0.0:8000`) |
| `RIVIU_FORWARDED_ALLOW_IPS` | Proxy được tin X-Forwarded-For (gateway mạng Docker `172.31.110.1`) |
| `RIVIU_SESSION_DAYS` | Thời hạn đăng nhập (mặc định 7 ngày) |
| `RIVIU_DATA_DIR` | Thư mục dữ liệu (container: `/data`) |

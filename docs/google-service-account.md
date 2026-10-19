# Tài khoản dịch vụ Google (service account)

Tài khoản dịch vụ là một tài khoản "robot" của Google Cloud, có email dạng
`riviu-reports@<project>.iam.gserviceaccount.com`. Nó đọc và ghi được mọi Google Sheet
được chia sẻ cho email đó với quyền **Người chỉnh sửa**, nên server dùng chung không phụ
thuộc vào tài khoản Google cá nhân của ai. Đây là cách **khuyên dùng**; đăng nhập Google
(OAuth) vẫn là cách thay thế.

Khi đã nạp tài khoản dịch vụ, ứng dụng dùng nó cho **Nạp sheet** (sheet riêng tư), tải
sheet và **Tạo sheet** (đẩy kết quả), ưu tiên hơn đăng nhập Google. Gỡ tài khoản dịch vụ thì
mọi thứ quay lại dùng đăng nhập Google như trước. Ứng dụng chỉ gọi Google Sheets API, với
quyền `https://www.googleapis.com/auth/spreadsheets`; không cần Google Drive API.

## Tạo tài khoản dịch vụ (admin, làm một lần)

1. Mở <https://console.cloud.google.com> và chọn **đúng project đang chứa OAuth client**
   hiện tại của ứng dụng (ô chọn project ở thanh trên cùng).
2. **APIs & Services → Library** → tìm **Google Sheets API** → **Enable** (nếu đã bật thì bỏ qua).
3. **IAM & Admin → Service Accounts → + Create service account**.
   - **Service account name**: ví dụ `riviu-reports` → **Create and continue**.
   - Bước **Grant this service account access to project**: bỏ qua, không chọn role → **Continue**.
   - Bước **Grant users access**: bỏ qua → **Done**.
4. Bấm vào tài khoản vừa tạo → tab **Keys** → **Add key → Create new key** → chọn **JSON** →
   **Create**. Trình duyệt tải về một file `.json`. File này là khoá bí mật: không gửi qua
   chat, không đưa lên Git; giữ một bản ở nơi an toàn hoặc xoá sau khi nạp.

## Nạp vào ứng dụng

1. Mở bảng **Nguồn dữ liệu** → bấm **Nạp tài khoản dịch vụ** → chọn file `.json` vừa tải.
2. Ô xanh hiện email tài khoản dịch vụ, nút **Copy** và dòng
   "Chia sẻ Google Sheet cho email này với quyền Người chỉnh sửa".
3. Với **mỗi** Google Sheet cần nạp hoặc tạo sheet kết quả: mở sheet → **Chia sẻ (Share)** →
   dán email tài khoản dịch vụ → chọn **Người chỉnh sửa (Editor)** → bỏ chọn "Thông báo" nếu
   muốn → **Gửi**. Có thể chia sẻ cả một thư mục Drive để các sheet bên trong dùng được luôn.
4. Nếu quên chia sẻ, Google trả lỗi 403/404 và ứng dụng báo
   "Tài khoản dịch vụ … chưa có quyền với Google Sheet này … Chia sẻ Google Sheet cho email …
   với quyền Người chỉnh sửa".

Gỡ: bấm **Gỡ tài khoản dịch vụ** trong ô xanh. Muốn vô hiệu hoá hẳn khoá cũ, xoá key đó ở
tab **Keys** trên Google Cloud.

## Lưu trữ

- File được kiểm tra trước khi lưu: phải có `"type": "service_account"`, `client_email` và
  `private_key`; `token_uri` (nếu có) phải là địa chỉ `googleapis.com`. Chỉ giữ các trường cần thiết.
- Lưu ở `data/google_service_account.json` trong thư mục dữ liệu (ghi atomic).
  Bản desktop lưu giống file OAuth client. **Chế độ server** mã hoá AES-256-GCM bằng khoá
  `RIVIU_SECRET_KEY_FILE` (cùng khoá với kho cookie), gắn với thư mục dữ liệu, nên file không
  chứa khoá hay email ở dạng rõ. Khoá không bao giờ được ghi vào log hay trả về API;
  `/google-oauth-status` chỉ trả email và trường `activeCredential`
  (`service_account`, `oauth` hoặc `none`).

## Khi không tạo được key

Nếu Google báo không cho tạo key (thường là tổ chức Google Workspace bật chính sách
**`iam.disableServiceAccountKeyCreation`**), cần **admin của tổ chức** cho phép tạo key cho
project này (IAM & Admin → Organization Policies → *Disable service account key creation* →
tắt hoặc thêm ngoại lệ cho project), hoặc tạo tài khoản dịch vụ trong một **project Google Cloud
cá nhân** (không thuộc tổ chức) rồi chia sẻ sheet cho email đó như trên.

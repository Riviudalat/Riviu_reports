# Sửa lỗi sau review — Riviu Reports

Đã xử lý 16 phát hiện trong [báo cáo review](2026-09-25_project-review-report.md) và lỗi quality workflow có thể bỏ qua exit code của pytest. Không chạy quét hàng loạt hoặc ghi Google Sheet trong quá trình sửa.

| Phát hiện | Thay đổi và kiểm chứng |
|---|---|
| F01 | Đích Google được lấy theo workbook; URL override chỉ áp dụng cho lần xuất, không sửa registry nguồn. Context phiên quét giữ cố định file/sheet/đích. Test A→B và đổi lựa chọn giữa phiên đều đạt. |
| F02–F03 | Upload/sync kiểm tra workbook trong file tạm, công bố bằng thao tác không ghi đè; tên trùng nhận suffix. File hỏng bị từ chối, bytes file cũ giữ nguyên. Đổi/nạp nguồn bị khóa khi đang quét hoặc cập nhật. File .xls cũ cần chuyển thành .xlsx. |
| F04 | Parser HTTP chỉ đọc module JSON thống kê của trang Threads; DOM được giới hạn theo permalink của bài. Caption và bài gợi ý không cung cấp lượt xem; browser chỉ bổ sung chỉ số HTTP còn thiếu. |
| F05–F06 | Cập nhật đối tác truyền đúng sheet modal. WebSocket có runId/platform/fileId/sheetName và snapshot khi kết nối lại; số thiếu vẫn trống và repost giữ đúng nhãn. Response cũ bị loại khi người dùng đổi file/sheet/nền tảng. |
| F07 | Hủy TikTok/Threads chờ lưu atomic và cleanup hoàn tất; không có hai writer chồng nhau. Lỗi lưu được báo thất bại, giữ nguyên workbook cũ. Hủy trước khi runner bắt đầu cũng giải phóng phiên. |
| F08 | SOCKS dùng kết nối riêng theo proxy; không thay socket/PySocks global. Test concurrent A/B/direct đạt. |
| F09 | Tên sheet Excel được sanitize, giới hạn 31 ký tự và xử lý trùng tên. Google import giữ mapping tên gốc trong custom property. Lỗi result tab không bỏ qua summary; tab kết quả không xuất hiện trong lựa chọn nguồn. |
| F10–F11 | Tổng dùng chung metric_number, hỗ trợ dấu phân cách số và pandas 3; REPOST hoàn toàn thiếu vẫn trống. Ngày Threads điền tiếp trong cùng nhóm, dừng ở ranh giới nhóm. |
| F12 | Cả hai nền tảng chỉ báo hoàn tất sau trạng thái completed từ server; phân biệt running/saving/failed/cancelled. |
| F13 | API local kiểm tra Host/Origin và cookie phiên; WebSocket từ origin ngoài hoặc thiếu phiên bị từ chối. Desktop control yêu cầu token riêng. |
| F14 | Google/OAuth/proxy network chạy ngoài event loop. Thao tác ghi chạy trong thread phải kết thúc trước khi nhả khóa khi bị hủy. |
| F15 | Biến Python được đặt trước block CMD; kiểm tra với shell mới đạt. Batch giữ CRLF không BOM. |
| F16 | Tauri cấp đúng hai quyền updater cho đúng origin/cổng của app. Backend giữ khóa cập nhật; frontend không cài update khi quét/lưu hoặc chưa nhận snapshot. |
| CI | pytest và node check là hai bước độc lập; cài Chromium cho test trình duyệt. Hai expectation cũ được chỉnh theo runner macOS hiện hành và quy tắc ngày dd/mm/yyyy. |

Kiểm chứng bản sửa: **252 test đạt, 3 test live tùy chọn bỏ qua**; JavaScript syntax và pip dependency check đạt. Test đầu-cuối với Chromium thật trên server local dùng workbook tạm xác nhận: chọn Sheet B không sửa Sheet A, reload khôi phục Threads, ô thiếu không thành 0, hoàn tất sau khi lưu. Link Threads mẫu công khai được kiểm riêng: HTTP/browser cùng 379 views; browser 4 likes, 1 comment, repost không hiển thị, 2 shares tại thời điểm kiểm tra.

Rust đã qua `cargo check --locked` với overlay chỉ bỏ externalBin trong lần kiểm tra vì sidecar executable chưa được đóng gói. Chưa build/install bản phát hành Windows/macOS/Linux, chưa thử cập nhật qua một release thật. Không coi kiểm tra biên dịch là bằng chứng nghiệm thu installer.

Bộ bàn giao nằm trong `output/review-fixes`: `MODIFIED_FILE.tar`, `DIFF_FILE.patch`, `VERIFICATION.txt`, `ROLLBACK.sh` và baseline sibling. Bản gốc trước lượt sửa được giữ riêng; rollback được thực thi trên bản sao kiểm thử, không áp dụng lên workspace người dùng.

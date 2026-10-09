# Threads: HTTP, Hybrid và proxy

## Chế độ quét
- **Request** gửi HTTP với `Accept` HTML và đọc JSON thống kê gắn đúng permalink. Không cần Chromium để lấy số được nhúng trong response.
- **Browser** chạy Chromium headless, đọc cả navigation response và JSON/DOM đã render của đúng bài.
- **Hybrid** lấy HTTP trước, chỉ khởi chạy Chromium khi HTTP (sau lần thử lại) vẫn thiếu một chỉ số core (lượt xem, tim, bình luận, repost) hoặc lỗi không thuộc nhóm terminal. Giá trị HTTP đã xác nhận (kể cả 0) được giữ nguyên.

HTTP có tối đa một lần thử lại cho lỗi kết nối tạm thời hoặc thiếu chỉ số core; headless bổ sung tối đa một lần. Không thử lại và không mở Chromium chỉ vì chia sẻ chưa có số: khi `reshare_count` của bài là null, trang Chromium render cũng null và nút Share không hiện số (kiểm live 08/10/2026: 0/4 lần fallback có thêm chia sẻ, mỗi lần 7,7–21 s). Chia sẻ thiếu vẫn để trống. Không đổi proxy để vượt 403/429 hoặc giới hạn người xem.

Số luồng là giới hạn chung cho mọi tải trang của phiên (HTTP và Chromium cộng lại), nên một tuyến proxy/phiên cookie không nhận nhiều yêu cầu đồng thời hơn số luồng. Ở Hybrid, browser fallback trả slot HTTP trước khi chờ Chromium, được tối đa 2 trang cùng lúc (luôn ít hơn số luồng khi có từ 2 luồng) và được ưu tiên slot trống trước link HTTP đang chờ; HTTP giữ các slot còn lại nên không bị fallback chậm chặn.

Hết link thì Chromium được đóng ngay trong lúc lưu workbook: chờ thoát tối đa 0,5 s, sau đó buộc dừng cả cây tiến trình Chromium (Windows), và chỉ chờ driver Playwright thêm tối đa 0,5 s; phần dọn dẹp chậm hơn chạy nền, cập nhật app vẫn chờ nó xong. Trên máy Windows đang tải nặng, tiến trình Chromium đã bị dừng vẫn có thể mất vài giây đến hơn một phút mới biến khỏi danh sách tiến trình; đó là thời gian hệ điều hành dọn tiến trình, không giữ phiên quét.

Số thiếu/null giữ trống, không phải 0. Query views chính xác trả null/mâu thuẫn sẽ xóa số views cũ (đặc biệt số header rút gọn) để không xuất số chưa còn được xác nhận; các metric khác thiếu tạm thời vẫn giữ quy tắc bảo toàn ô hiện có. Không lấy số trong caption, bài gợi ý hoặc Home sau chuyển hướng. Một biến thể JSON có số nhưng ngoài format đã xác minh có thể vẫn cần browser fallback; chỉ bổ sung parser format mới khi có fixture thể hiện provenance.

## Proxy
Nút proxy dùng chung ở cả TikTok và Threads. Với Threads:
- Danh sách đang nhập được ưu tiên; nếu để trống thì dùng danh sách đã lưu.
- Dòng sai hoặc danh sách không có proxy enabled bị từ chối trước khi quét; không tự dùng kết nối direct.
- Pool proxy thuộc phiên Threads, chia round-robin theo URL đã gộp trùng.
- Một URL giữ cùng proxy trong HTTP, retry và Browser fallback.
- HTTP dùng transport proxy hiện có; Browser dùng context riêng qua cùng proxy, đóng khi hoàn tất/hủy.
- HTTP proxy có authentication và SOCKS5 không authentication dùng được với Browser/Hybrid. Chromium không hỗ trợ SOCKS5 authentication: dùng Request hoặc đổi sang HTTP proxy. Không bỏ credential để thử chạy.
- Log lỗi Threads không chứa URL proxy xác thực, username hoặc password.

## Nguồn theo nền tảng
Drawer nguồn có hai dòng **TikTok** và **Threads** riêng, mỗi dòng lưu link, workbook và sheet đã chọn. Nạp dòng không hoạt động không đổi workbook đang hiển thị; khi đổi nền tảng, app chờ xác nhận chọn đúng file trước khi cho quét. Tạo sheet/xuất/download/preview gửi file ID rõ ràng, không dùng nguồn của dòng khác. Nguồn Google gốc vẫn gắn workbook, URL override chỉ dùng cho lần xuất. Nguồn Threads chưa chọn không tự lấy file TikTok. Preferences file/sheet/link không chứa cookie được lưu localStorage; cookie vẫn ở kho OS.

## Cookie đăng nhập
Tab Threads có cụm **Cookie Threads**: switch dùng cookie, trạng thái và nút **Cấu hình**. Modal tách **Phiên đã lưu** (số cookie/thời điểm, Kiểm tra lại, Xóa phiên có xác nhận) và **Import phiên mới** với tab **Chọn file JSON / Dán JSON**. Không tự bật dùng cookie sau import. Hỗ trợ **Cookie-Editor → Export → JSON**: ở trang `threads.com`, copy/dán nguyên mảng JSON hoặc lưu thành `.json` rồi chọn file. Không dùng export Header/Netscape. Các trường `expirationDate`, `hostOnly`, `httpOnly`, `secure`, `sameSite` (`null`, `lax`, `strict`, `no_restriction`) được chuẩn hóa; `session`, `storeId` và metadata không cần gửi vào Chromium. Không giải mã/thay đổi giá trị cookie percent-encoded hay chuỗi có escape. Modal cũng nhận `{cookies:[...]}`; Import kiểm trực tiếp qua trang chủ Threads trước khi lưu. **Kiểm tra lại** thực hiện kiểm mới; **Xóa cookie** xóa khỏi kho và RAM. Không đưa cookie vào log, localStorage hoặc báo cáo.

Windows lưu blob mã hóa **DPAPI current-user** dưới data root; macOS dùng **Keychain**, Linux dùng **Secret Service** native qua keyring. Không có plaintext fallback. Kho không khả dụng/khóa/giải mã lỗi sẽ báo lỗi; không tự thay phiên cũ. DPAPI không bảo vệ khỏi phần mềm chạy cùng Windows user. Không thêm cookie thật vào git; cookie đã gửi trong hội thoại nên thu hồi phiên rồi export phiên mới.

“Đã import/lưu” khác “hợp lệ”. Valid cần dữ liệu viewer đăng nhập có provenance, kèm thời điểm kiểm; không suy từ HTTP200 hoặc cookie chưa hết hạn. Sau restart, status là chưa kiểm; trước scan luôn có preflight trên tuyến đã chọn. Lỗi mạng/schema chưa xác định khác hết hạn; checkpoint cần người dùng xác minh trong Threads, app không tự giải.

Cookie theo **toggle proxy của phiên quét**: proxy tắt dùng direct, proxy bật dùng **proxy enabled đầu tiên** xuyên suốt; không có lựa chọn cookie/proxy riêng và không xoay nhiều tuyến cho một tài khoản. App không chuyển direct/anonymous nếu tuyến đã chọn thất bại. Cookie luôn được kiểm qua Chromium, nên khi bật cookie thì proxy đầu tiên phải là HTTP proxy hoặc SOCKS5 không xác thực; SOCKS5 có tài khoản/mật khẩu bị từ chối ngay khi bắt đầu quét (trạng thái `proxy_unsupported`) và không bị coi là cookie hỏng. HTTP sử dụng CookieJar riêng, Browser context riêng, redirects bị giới hạn HTTPS Threads. Phiên bị từ chối hoặc không còn xác minh được giữa lượt sẽ dừng, giữ kết quả đã lưu và yêu cầu kiểm lại.

Một bài có thể trả geoblock trong phiên không đăng nhập nhưng mở được trong phiên đăng nhập đã xác minh. Chỉ so sánh/kết hợp nguồn trong cùng snapshot phiên; không tự lấy views anonymous để bù cho authenticated. Views logged-in ưu tiên query `BarcelonaPostViewCountQuery.text_post_app_info.impression_count` nối **media.id** với target payload đã khớp shortcode/username. Parser nhận Relay module chuẩn và biến thể `@32hex` đã xác minh. Exact count được giữ (ví dụ15174), không dùng header15.1K thay thế. Nếu không có query chính xác thì fallback header permalink `Column title`/`h1` và href đúng bài; bộ đếm rút gọn như `2.7K views` quy đổi2700 chỉ là số hiển thị, có provenance `header_display`. Query có null/mâu thuẫn giữ unknown, không tự backfill bằng text. Header/counter không có hoặc mâu thuẫn vẫn giữ trống; shares null không suy0. Guard dùng header điều hướng HTML, fetch từng hop HTTPS đã kiểm rồi điều hướng riêng tới final URL; không cho Chromium tự theo redirect chưa kiểm. Bài có số liệu khác xác nhận vẫn báo OK. Cookie hợp lệ không bảo đảm đủ5 chỉ số hoặc mọi link invalid_post hoạt động.

## Trạng thái
- **Giới hạn theo vị trí**: root route Threads xác nhận geoblock **trong phiên hiện tại**. Mục See Why có thể giải thích yêu cầu pháp lý theo địa điểm. Mở trang thông báo không phải mở nội dung; đăng nhập có thể thay đổi kết quả ở một số bài đã kiểm, không bảo đảm giải quyết tất cả. Không tự đổi proxy/vị trí để vượt chặn.
- **Giới hạn người xem**: thông báo audience chung nhưng chưa xác nhận geoblock. Không đồng nghĩa bài đã xóa hoặc nhất thiết do chưa đăng nhập.
- **invalid_post**: URL chuyển về lỗi invalid_post; không lấy số của Home thay thế. Kiểm lại permalink từ bài mở được.
- **OK / Success**: đọc được ít nhất một số liệu đã xác nhận, kể cả **0**. Không yêu cầu đủ5 chỉ số để quét thành công. Ô nguồn chưa trả vẫn để trống, có chi tiết `missingMetrics` trong tooltip/log; không suy null thành0. Hybrid vẫn bổ sung chỉ số core còn thiếu, không dừng sớm chỉ vì trạng thái OK; thiếu riêng chia sẻ không mở Chromium.
- **Không số liệu**: không có chỉ số nào được xác nhận. **Lỗi**: đọc bài/truy cập/phiên thất bại. Nhãn Partial cũ được hiển thị OK trong Live nếu có số đã xác nhận, không sửa ngược workbook cũ.
- **Link hồ sơ**: chỉ có `/@username`, không có `/post/<shortcode>`; log vị trí cần sửa link và không thay số của dòng đó.
- **Link Threads không hợp lệ** (ví dụ `/@user/post/` thiếu mã bài, `/post/<code>/media`, link cũ `/t/<code>`): log rõ sheet/dòng để sửa link, được tính vào tổng kết phiên và không ghi số vào dòng đó.

Hủy quét chờ HTTP thread đang chạy và writer atomic hoàn tất trước khi nhả phiên. HTTP đang chạy có thể cần hết timeout; không bảo đảm hủy tức thì.

## Báo cáo đối tác và Google Sheet
Báo cáo Threads giống hệt TikTok: 8 cột NGÀY AIR, TÊN KÊNH, LINK AIR, LƯỢT XEM, TIM, BÌNH LUẬN, REPOST (ở vị trí LƯỢT LƯU), CHIA SẺ; không có cột trạng thái. Áp dụng cùng ngưỡng lượt xem tối thiểu (mặc định 100, bật/tắt như TikTok) và cùng bộ lọc kênh lỗi, nên link 0 hoặc chưa rõ lượt xem không được báo cáo khi ngưỡng đang bật. Ô TÊN KÊNH trống hiển thị `@username` lấy từ link bài. Dòng TỔNG cộng các số đã biết, chỉ để trống khi cả cột chưa có số nào. Đẩy Google Sheet dùng cùng cột như TikTok (REPOST thay LƯỢT LƯU), không có cột Trạng thái; trạng thái quét vẫn nằm ở cột ẩn `__THREADS_SCAN_STATUS` trong workbook.

## Sheet Tổng kết
Quét Threads hoàn tất sẽ tạo/cập nhật tab **Tổng kết Threads <tên sheet>** ngay sau sheet dữ liệu (sau tab Tổng kết TikTok nếu có). Mỗi đối tác có số link Threads và tổng LƯỢT XEM, TIM, BÌNH LUẬN, REPOST (ở vị trí LƯỢT LƯU), CHIA SẺ; tổng cộng các số đã biết và chỉ để trống khi đối tác chưa có số nào. Quét bị hủy hoặc lỗi không cập nhật tổng kết. Sheet chứa cả link TikTok và Threads có hai tab riêng: `Tổng kết <tên sheet>` chỉ đếm link TikTok và chỉ do quét TikTok cập nhật, `Tổng kết Threads <tên sheet>` chỉ đếm link Threads và chỉ do quét Threads cập nhật; quét nền tảng này không ghi đè tổng kết của nền tảng kia. Mở một tab tổng kết luôn hiển thị đúng số liệu của tab đó. Thanh tab của mỗi nền tảng chỉ hiện sheet dữ liệu và tab tổng kết của chính nền tảng đó.

## Chẩn đoán phiên
Các phiên mới lưu tối đa 50 bản tóm tắt trong `data/threads_scan_history.json` dưới data root của app: mode, workers, proxy bật/count/type, file/sheet, số URL duy nhất và số dòng, success/partial/error theo loại, thời điểm và completed/cancelled/failed. Không lưu proxy host/user/password, cookie/token, link/xmt hoặc exception text. Lưu atomic sau cleanup; lỗi lưu chẩn đoán không làm mất kết quả workbook. Đây là file chẩn đoán riêng, chưa hiển thị trong bảng lịch sử TikTok và không tái dựng được cấu hình các phiên cũ.

## Kiểm chứng
Test transport: `tests/test_threads_transport.py` kiểm Accept, retry, terminal errors, route consistency, cleanup/cancellation và HTTP+Chromium thật qua proxy loopback giả. Test UI/backend nằm trong `tests/test_ui_review_fixes.py` và `tests/test_backend_review_fixes.py`.

Chạy bộ test bằng `uv run pytest -q tests` để không thu thập các bản sao test trong thư mục output. Test nguồn/live không thay kiểm installer/Tauri. Không dùng proxy production hoặc ghi Google Sheets khi chạy fixture.

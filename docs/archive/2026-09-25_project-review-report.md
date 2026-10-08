# Review toàn bộ Riviu Reports — 25/09/2026

## Kết luận và phạm vi

Review tìm thấy **16 vấn đề cần xử lý**, gồm 2 vấn đề ưu tiên P1 về ghi nhầm đích và mất file, cùng 14 vấn đề P2 về tính đúng đắn, vận hành và bảo vệ ứng dụng local. Review bao gồm working tree hiện tại, cả phần Threads chưa commit, trên HEAD `0bccf433c34933eadcb8c74969d9aa5350d4e91e`. Phạm vi: FastAPI/WebSocket, TikTok/Threads scraper, proxy, Excel, Google Sheets/OAuth, JavaScript/UI, Tauri, script Windows và release workflow.

Người dùng yêu cầu review toàn bộ dự án. Các phép tái hiện dùng workbook tạm, transport/API giả lập, JS harness và Chromium headless với DOM fixture. Không sửa source, không chạy quét hàng loạt, không gửi dữ liệu hoặc ghi Google Sheet. Một GET công khai đọc link Threads mẫu đã được người dùng cung cấp; kết quả đó không được dùng để suy diễn mọi bài đều quét được. Báo cáo và bản ghi kiểm chứng là các file mới của lượt review.

P1: nên sửa trước khi dùng để xuất/ghi dữ liệu thật. P2: lỗi có tình huống cụ thể, cần sửa trong đợt tiếp theo. Đây là review code và kiểm chứng cục bộ, chưa phải kiểm thử bản cài desktop trên tất cả hệ điều hành.

## Phát hiện ưu tiên cao

### F01 — P1: Chọn workbook B nhưng vẫn đẩy sang Google Sheet A

- **Vị trí:** [app.py:770](../../riviu/app.py#L770), [app.py:792](../../riviu/app.py#L792), [app.py:933](../../riviu/app.py#L933).
- **Đường lỗi:** đồng bộ A → chọn B → tạo tab kết quả. `GOOGLE_SHEET_SOURCE_URL` ưu tiên hơn registry theo file và không được cập nhật khi đổi file. `push_google_sheet` còn ghi đích này vào registry của B trước khi hoàn tất thao tác.
- **E01 — Tái hiện:** hai workbook tạm có registry `SHEET_A`/`SHEET_B`; gọi `select_file(B)` rồi `push_google_sheet` với Google write được mock. Kết quả: `current=B.xlsx`, `GOOGLE_DESTINATION=.../SHEET_A/edit`, `ACTUAL_DEST=['SHEET_A']`, `EXPECTED=SHEET_B`.
- **Tác động:** xuất kết quả của đối tác/file B vào tài liệu A; có thể lộ dữ liệu giữa các tài liệu được chia sẻ cho nhóm khác nhau.
- **Hướng sửa:** gắn file, source sheet và spreadsheet ID vào một context bất biến cho mỗi thao tác; URL đích chỉnh tay phải là override rõ ràng, không làm thay đổi nguồn gốc workbook.

### F02 — P1: Upload hỏng hoặc trùng tên phá hủy workbook đang có

- **Vị trí:** [app.py:1116](../../riviu/app.py#L1116).
- **Đường lỗi:** upload `B.xlsx` khi đã có B. Endpoint mở đích bằng `wb` trước khi kiểm tra nội dung. `default_sheet_for_file` nuốt lỗi parse, sau đó endpoint vẫn trả `success: true`.
- **E02 — Tái hiện:** workbook B hợp lệ → upload nội dung `b'invalid workbook'` cùng tên. Literal output: `{'success': True, 'filename': 'B.xlsx', 'sheet': '', 'scanSheet': ''}`; file trên đĩa còn đúng `b'invalid workbook'`.
- **Hướng sửa:** đọc vào file tạm, kiểm tra workbook/schema, chọn tên không trùng hoặc giữ bản sao, rồi atomic replace sau khi hợp lệ. Chặn ghi vào workbook đang được scan.

## Tính đúng đắn của quét và báo cáo

### F03 — P2: Đồng bộ cùng phút có thể ghi đè bản Excel trước

- **Vị trí:** [app.py:814](../../riviu/app.py#L814), [workbook_utils.py:262](../../riviu/workbook_utils.py#L262).
- **E03:** mock hai spreadsheet ID khác nhau có cùng title và timestamp phút. Hai lần sync đều trả `data/Same Name 25-09-2026-10-30.xlsx`; `SAME_DESTINATION=True`. Sync lại cùng sheet trong một phút cũng đi cùng đường ghi đè.
- **Tác động:** bản local vừa quét/chỉnh có thể bị thay bằng dữ liệu mới từ Google dù thông báo gọi đó là “file mới”.
- **Hướng sửa:** thêm ID nguồn và định danh lần tải không trùng; dùng tạo file độc quyền hoặc suffix khi tồn tại.

### F04 — P2: Threads đọc số trong caption thành lượt xem

- **Vị trí:** [threads_scraper.py:135](../../riviu/platforms/threads.py#L135), [threads_scraper.py:98](../../riviu/platforms/threads.py#L98).
- **E04:** Chromium thật, DOM fixture có caption `999999 views this week!` đứng trước widget `377 views`; cùng URL và username hợp lệ. Output: `HTTP actual views: 377`, `Browser views: 999999`, `Hybrid merged views: 999999`.
- **Nguyên nhân/tác động:** regex tìm trên toàn `body`, rồi merge ưu tiên kết quả browser nên ghi đè số HTTP đúng. Các selector action cũng đang dùng phần tử đầu tiên toàn trang, chưa được giới hạn trong container của bài đích.
- **Hướng sửa:** định vị container bài bằng permalink/post identity và widget số liệu của chính bài; không lấy số từ nội dung caption/replies/recommendations.

### F05 — P2: “Cập nhật đối tác” quét sheet ở toolbar thay vì sheet trong modal

- **Vị trí:** [static/app.js:1344](../../riviu/web/static/app.js#L1344), [static/app.js:1252](../../riviu/web/static/app.js#L1252).
- **E05:** JS harness chạy hàm hiện tại, toolbar chọn A và modal chọn B. Request nhận được: `{"action":"start","platform":"threads","sheet_name":"Sheet A","partners":["Partner B"]}`.
- **Tác động:** nếu cùng đối tác xuất hiện ở A và B, app sửa A trong khi người dùng yêu cầu B; nếu không, không cập nhật dòng nào ở B.
- **Hướng sửa:** truyền `reportSheetName` rõ ràng vào lệnh quét đối tác, không đọc lại lựa chọn toolbar.

### F06 — P2: F5/tab mới khi đang quét Threads làm sai nhãn và biến số thiếu thành 0

- **Vị trí:** [static/app.js:17](../../riviu/web/static/app.js#L17), [static/app.js:1219](../../riviu/web/static/app.js#L1219), [threads_scraper.py:288](../../riviu/platforms/threads.py#L288).
- **E06:** nền tảng UI khởi tạo lại là TikTok; event WebSocket không có platform/run ID và phát cho mọi kết nối. Harness xác nhận `threadsNullMetricRenderedAs='0'`, nhãn repost thành `LƯỢT LƯU`.
- **Tác động:** kết quả mới tiếp tục về sau reload nhưng được diễn giải theo nền tảng sai; vi phạm yêu cầu giữ chỉ số không đọc được là trống.
- **Hướng sửa:** event có `platform`, `runId`, `fileId`, `sheetName`; kết nối lại nhận snapshot phiên đang chạy. Render theo metadata của kết quả thay vì lựa chọn UI hiện tại.

### F07 — P2: Hủy TikTok mất kết quả đã hiển thị nhưng chưa autosave

- **Vị trí:** [scraper.py:3041](../../riviu/platforms/tiktok.py#L3041), [scraper.py:3115](../../riviu/platforms/tiktok.py#L3115).
- **E07:** workbook tạm có view=10; worker mock trả Success view=999; hủy ngay khi nhận data event. Output: `Websocket completed first result: 999 Success`, `Task cancelled`, `Saved workbook first result after cancel: 10`.
- **Tác động:** cancel đi qua cleanup nhưng bỏ qua final save. Mặc định autosave 25 có thể mất tối đa 24 kết quả mới, ngưỡng thích ứng lên 100 làm khoảng mất lớn hơn.
- **Hướng sửa:** cancellation phải chờ lưu pending workbook an toàn, rồi mới phát trạng thái đã hủy; kiểm thử cả trường hợp file bị khóa.

### F08 — P2: Proxy SOCKS bị dùng chéo giữa worker

- **Vị trí:** [proxy_utils.py:92](../../riviu/proxy_utils.py#L92), [proxy_utils.py:47](../../riviu/proxy_utils.py#L47).
- **E08:** dùng ThreadPoolExecutor và PySocks thật, chỉ mock HTTP transport để xem proxy của socket. Output: `Worker A expected proxy-a for both: ('proxy-a.example', 'proxy-b.example')`; `Worker B: proxy-b.example`.
- **Nguyên nhân:** `socket.socket` và PySocks default proxy là global, nhưng kiểm tra cấu hình bằng thread-local. Một worker HTTP/direct còn có thể restore socket global trong lúc worker SOCKS dùng nó.
- **Hướng sửa:** transport/socket được gắn proxy theo request/session; không monkeypatch socket toàn process.

### F09 — P2: Dấu `:` trong tên tab phá tạo Excel và nhập lại Google Sheet

- **Vị trí:** [workbook_utils.py:57](../../riviu/workbook_utils.py#L57), [scraper.py:677](../../riviu/platforms/tiktok.py#L677), [google_sheets_sync.py:360](../../riviu/google_sheets_sync.py#L360).
- **E09:** `scraper.build_result_sheet(Workbook(), [], '')` trả `ValueError: Invalid character : found in sheet title`. Sheets API fixture có tab `T9 25-09-2026-10:30` gây cùng lỗi ở authenticated import.
- **Tác động:** bật `create_result_sheet` không tạo được tab và bỏ qua cả rebuild summary do chung khối try. Chính app tạo tên tab Google chứa `:`; lần nạp lại qua API thất bại. Với sheet riêng tư, public fallback không giải quyết được.
- **Hướng sửa:** tách naming Google/Excel, sanitize ký tự cấm, giữ mapping tên gốc sang tên Excel duy nhất sau giới hạn 31 ký tự; lỗi tạo result tab không được bỏ qua summary.

### F10 — P2: Preview lỗi với số có phân cách hàng nghìn

- **Vị trí:** [workbook_utils.py:628](../../riviu/workbook_utils.py#L628).
- **E10:** workbook thật trong thư mục tạm có `TIM='1,234'` hoặc `'1.234.567'` và dòng `TỔNG`. Trên pandas 3.0.5, `read_sheet_preview` ném `TypeError Invalid value '0' for dtype 'str'`. Hàm `metric_number` hiện có lại đọc đúng 1234/1234567.
- **Nguyên nhân:** `pd.to_numeric(..., errors='coerce')` làm số dạng này thành NaN rồi 0; ghi tổng int vào cột string gây exception ở pandas 3. Trên phiên bản cho phép assignment, nguy cơ là tổng sai thay vì exception.
- **Hướng sửa:** dùng thống nhất parser số, kiểm soát dtype của bảng hiển thị và có test với dữ liệu Sheets định dạng số.

### F11 — P2: Báo cáo Threads bỏ ngày ở các dòng dùng chung ngày đầu nhóm

- **Vị trí:** [workbook_utils.py:646](../../riviu/workbook_utils.py#L646).
- **E11:** workbook có ngày ở dòng Threads đầu, dòng kế để trống. `build_workbook_rows(..., platform='threads')` trả `['25/09/2026', 'nan']`; cùng kiểu nhóm ngày với TikTok được điền tiếp.
- **Hướng sửa:** forward-fill cho các loại link hỗ trợ theo cùng quy tắc nhóm; thêm test cho ô ngày gộp và ranh giới nhóm.

### F12 — P2: TikTok vẫn báo “Thành công” khi phiên quét/lưu file lỗi

- **Vị trí:** [static/app.js:1403](../../riviu/web/static/app.js#L1403), [app.py:605](../../riviu/app.py#L605).
- **E12:** chạy hàm `updateProgress` thật với `{total:0,processed:0,success:0,error:1,done:true}`. Output: `status='Thành công'`, `statusClass='progress-status success'`, `scanCompleted=true`.
- **Nguyên nhân:** nhánh lỗi chỉ áp dụng cho Threads; TikTok còn coi `processed==total` là hoàn tất trước khi final save thành công.
- **Hướng sửa:** trạng thái phiên rõ ràng `running/saving/completed/failed/cancelled`; chỉ báo thành công sau server xác nhận lưu thành công.

## An toàn ứng dụng và vận hành

### F13 — P2: WebSocket local chấp nhận origin ngoài ứng dụng

- **Vị trí:** [app.py:100](../../riviu/app.py#L100), [app.py:1129](../../riviu/app.py#L1129).
- **E13:** TestClient kết nối `/ws` với `Origin: https://untrusted.example`; gửi `cancel` gọi được `cancel()` của job fixture và nhận log xác nhận. Không hủy phiên thật.
- **Tác động:** server không tự bảo vệ lệnh quét/hủy và luồng kết quả trước website khác kết nối loopback. Việc trình duyệt cụ thể có cho truy cập local network còn phụ thuộc chính sách trình duyệt; không giả định mọi trình duyệt đều khai thác được.
- **Hướng sửa:** kiểm tra Origin hợp lệ của app, xác thực capability/token phiên local cho API/WS và xác minh Host. Binding loopback không thay thế kiểm tra này. Non-goal auth của bản kế hoạch cũ không làm mất quan sát này; đây là đề xuất cho lượt review mới, không phải thay đổi đã áp dụng.

### F14 — P2: Google/OAuth và proxy test đồng bộ làm đứng event loop

- **Vị trí:** [app.py:916](../../riviu/app.py#L916), [app.py:813](../../riviu/app.py#L813), [app.py:890](../../riviu/app.py#L890).
- **E14:** patch `authorize_google` bằng lời gọi blocking 250ms, chạy song song heartbeat asyncio 20ms. Heartbeat thực tế 250ms. Các endpoint async gọi trực tiếp thao tác network/OAuth đồng bộ.
- **Tác động:** trong lúc chờ người dùng đăng nhập Google hoặc request chậm, các request khác/WebSocket và lệnh hủy bị trì hoãn trên cùng event loop.
- **Hướng sửa:** đưa blocking I/O vào thread executor hoặc endpoint sync phù hợp; khóa scope thao tác dữ liệu, thêm timeout và kiểm thử khả năng đáp ứng khi OAuth đang chờ.

### F15 — P2: `capnhat.bat` không cập nhật dependency từ shell mới

- **Vị trí:** [capnhat.bat:155](../../capnhat.bat#L155).
- **E15:** chạy đúng block dependency update trong thư mục tạm với Python giả. `VENV_PY` được set và đọc `%VENV_PY%` trong cùng block, CMD expand trước khi chạy; exit 9009, stderr `'""' is not recognized as an internal or external command`.
- **Hướng sửa:** đặt biến trước block hoặc dùng delayed expansion đúng cách; test với environment chưa có `VENV_PY`.

### F16 — P2: Desktop updater không được cấp quyền IPC từ trang loopback

- **Vị trí:** [src-tauri/capabilities/default.json:5](../../src-tauri/capabilities/default.json#L5), [src-tauri/src/lib.rs:146](../../src-tauri/src/lib.rs#L146).
- **E16 — Phân tích source/config:** app chuyển webview từ local asset sang `http://127.0.0.1:<port>` nhưng capability chỉ local, không có remote scope cho `check_for_update`/`install_update`. Đối chiếu Tauri 2.11.5 trong Cargo registry, `src/webview/mod.rs:1698` và `1819`: URL loopback này là non-local origin và custom command không có ACL sẽ bị từ chối. Frontend chỉ `console.warn` lỗi.
- **Giới hạn:** chưa build/chạy gói Tauri để quan sát runtime rejection; đây là kết luận theo dependency đã khóa và cấu hình hiện tại.
- **Hướng sửa:** cấp quyền hẹp cho webview/origin ứng dụng và đúng hai command hoặc đổi cách phục vụ frontend. Đồng thời chặn tự restart để update trong lúc scan; restart giữa scan là rủi ro tiềm ẩn hiện chưa đi qua ACL.

## Kiểm chứng và giới hạn

| Kiểm tra | Kết quả |
|---|---|
| `.venv\Scripts\python.exe -m pytest -q` | Exit 1; **198 passed, 2 failed, 3 skipped**; 198.19 giây |
| `node --check static/app.js` | Exit 0 |
| `.venv\Scripts\python.exe -m pip check` | Exit 0; `No broken requirements found.` |
| `git diff --check` | Exit 0; cảnh báo đổi LF/CRLF, không có lỗi whitespace |
| Bộ test scraper/proxy/Threads hẹp | Exit 0; **106 passed, 3 skipped** |

Hai test lỗi: [test_app_helpers.py:109](../../tests/test_app_helpers.py#L109) còn yêu cầu `macos-13` trong khi workflow dùng `macos-15-intel`; [test_app_helpers.py:223](../../tests/test_app_helpers.py#L223) yêu cầu giữ `02-06-2026` trong khi code chuẩn hóa thành `02/06/2026`. Chưa có bằng chứng đảo ngày/tháng. Cần thống nhất hợp đồng rồi sửa expectation; không nên chỉ đổi logic sản phẩm để làm xanh assertion cũ.

Ba test bỏ qua cần biến môi trường để chạy live post/share và UI. Không chạy lại các test này trong full-suite review, không quét toàn bộ 35 link thật. Không build gói macOS/Linux/Windows hoặc phát hành trong lượt này.

Lưu ý quality workflow tại [.github/workflows/desktop-release.yml:35](../../.github/workflows/desktop-release.yml#L35): pytest và node chạy nối tiếp trong một bước PowerShell. Tái hiện local cho thấy exit pytest 1 bị node exit 0 ghi đè; shell kết thúc 0. Chưa xác minh một run GitHub hosted cụ thể, vì vậy không kết luận hai test đỏ chắc chắn chặn release. Nên tách hai bước hoặc kiểm tra `$LASTEXITCODE` ngay sau pytest.

Bản ghi bổ sung: [output/review-verification.txt](../../output/review-verification.txt). File này được Git ignore. Môi trường kiểm tra: Python 3.14.7, pandas 3.0.5, openpyxl 3.1.5; CI dùng Python 3.12 nên phần khác biệt runtime cần được phủ test riêng.

## Thứ tự sửa đề xuất

1. F01/F02/F03: khóa đích theo workbook và bảo toàn file trước mọi overwrite.
2. F04/F05/F06/F07/F12: đúng bài, đúng sheet, đúng nền tảng và lưu trước khi báo hoàn tất/hủy.
3. F08/F09/F10/F11: proxy theo request, tên tab hợp lệ, parser số và ngày dùng chung.
4. F13/F14/F15/F16: bảo vệ local API, tránh blocking loop, sửa script cập nhật và quyền desktop updater.
5. Điều chỉnh hai assertion stale và đảm bảo quality gate không nuốt exit code của pytest.

Sau sửa cần chạy regression fixture cho từng lỗi rồi full suite; không dùng số test pass làm bằng chứng thay thế cho tính đúng đắn từng đường dữ liệu.

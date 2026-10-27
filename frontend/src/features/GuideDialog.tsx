import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { closeModal, useApp } from '@/state/app';

// Content of riviu/web/templates/guide.html. Screenshots stay under /static/guide/ (served by
// the backend); the numbered badges match the red callouts in each screenshot.

function N({ children }: { children: ReactNode }) {
  return <span className="mx-0.5 inline-flex size-5 items-center justify-center rounded-full bg-red-600 align-text-bottom text-[11px] font-bold text-white">{children}</span>;
}

function Shot({ src, alt }: { src: string; alt: string }) {
  return <img src={`/static/guide/${src}`} alt={alt} loading="lazy" width={1280} height={800} className="my-2 h-auto w-full rounded-lg border" />;
}

const STEPS: Array<{ title: string; body: ReactNode; adminOnly?: boolean }> = [
  { title: 'Đăng nhập', body: <><p>Mở địa chỉ server, nhập tài khoản và <b>Mật khẩu</b> <N>1</N> rồi bấm <b>Đăng nhập</b> <N>2</N>.</p><Shot src="01-dang-nhap.png" alt="Trang đăng nhập" /></> },
  { title: 'Chọn nền tảng và mở nguồn dữ liệu', body: <><p>Bấm <b>TikTok</b> trên thanh nền tảng <N>1</N>, rồi bấm <b>Đổi nguồn</b> <N>2</N> ở góc trên để mở bảng <b>Nguồn dữ liệu</b>.</p><Shot src="02-chon-nen-tang.png" alt="Thanh nền tảng và nút Đổi nguồn" /></> },
  { title: 'Nạp Google Sheet hoặc file Excel', adminOnly: true, body: <><p>Dán link vào ô <b>Link Google Sheet TikTok</b> <N>1</N> rồi bấm <b>Nạp sheet</b> <N>2</N>: ứng dụng tải sheet về thành một file mới. Hoặc bấm vùng <b>Nạp file Excel</b> <N>3</N> để tải lên file .xlsx. File đã nạp trước đó chọn lại ở <b>File hiện có</b>.</p><p>Sheet chia sẻ “Bất kỳ ai có đường liên kết” nạp được mà không cần đăng nhập Google. Sheet riêng tư cần tài khoản dịch vụ được chia sẻ sheet, hoặc đăng nhập Google.</p><Shot src="03-nap-nguon.png" alt="Bảng Nguồn dữ liệu" /></> },
  { title: 'Chọn sheet cần quét', body: <><p>Chọn tab dữ liệu cần quét ở ô <b>Sheet quét</b> <N>1</N> (nhân viên: <b>Sheet đang xem</b>). Để kiểm tra nội dung, bấm tab cùng tên <N>2</N> trên hàng tab sheet của <b>Dữ liệu sheet</b>.</p><Shot src="04-chon-sheet.png" alt="Ô Sheet quét" /></> },
  { title: 'Chọn chế độ quét và proxy', adminOnly: true, body: <><p>Ô <b>Chế độ</b> <N>1</N>: <b>Request</b> nhanh và nhẹ, nên dùng mặc định. <b>Trình duyệt</b> mở Chromium cho từng link, chậm hơn nhưng đọc được trang mà Request không đọc được. <b>Hybrid (Request + trình duyệt)</b> chạy Request trước rồi chỉ đưa link lỗi sang trình duyệt.</p><p><b>Luồng</b> <N>2</N> là số link quét song song. Khi bị chặn hoặc giới hạn IP, bật <b>Proxy xoay</b> <N>3</N> và bấm <b>Cấu hình</b> để dán và test proxy. <b>Cookie TikTok</b> <N>4</N> là tùy chọn.</p><Shot src="05-che-do-quet.png" alt="Chế độ, Luồng, Proxy xoay và Cookie TikTok" /></> },
  { title: 'Cookie TikTok (tùy chọn)', adminOnly: true, body: <><p>Một số bài (thường là bài ảnh) báo “Video không khả dụng” với người xem chưa đăng nhập nhưng vẫn xem được khi đăng nhập. Cookie TikTok cho phép quét các bài này bằng phiên đăng nhập.</p><p><b>Dùng tài khoản TikTok phụ</b> <N>1</N>, không dùng tài khoản chính. Cài tiện ích Cookie-Editor, đăng nhập tài khoản phụ ở tiktok.com, chọn <b>Export → JSON</b>. Trong <b>Cookie TikTok</b> → <b>Cấu hình</b>, chọn <b>Chọn file JSON</b> hoặc <b>Dán JSON</b> <N>2</N> rồi bấm <b>Import &amp; kiểm tra</b> <N>3</N>. Bật công tắc cạnh <b>Cấu hình</b> để dùng cookie cho lần quét đó. Cookie không bao giờ ghi vào nhật ký hay file kết quả.</p><Shot src="05b-cookie-tiktok.png" alt="Hộp thoại Cookie TikTok" /></> },
  { title: 'Bắt đầu quét và theo dõi', body: <><p>Bấm <b>Bắt đầu quét</b> <N>1</N> (quản trị). Thanh tiến trình <N>2</N> cho biết đã xử lý bao nhiêu link. Tab <b>Live</b> <N>3</N> hiện từng kết quả mới; <b>Thành công</b>, <b>Không số liệu</b>, <b>Lỗi quét</b> và <b>Link trùng</b> lọc theo trạng thái; <b>Nhật ký</b> ghi chi tiết từng bước.</p><Shot src="06-dang-quet.png" alt="Đang quét, tab Live" /></> },
  { title: 'Dừng phiên quét', adminOnly: true, body: <><p>Bấm <b>Hủy</b> <N>1</N> để dừng. Kết quả đã quét được vẫn lưu vào file; link chưa quét giữ nguyên số cũ. Tab <b>Lỗi quét</b> <N>2</N> liệt kê link lỗi kèm lý do, có nút <b>Copy link</b> để quét lại sau.</p><Shot src="07-huy-quet.png" alt="Nút Hủy và tab Lỗi quét" /></> },
  { title: 'Xem kết quả', body: <><p>Khi quét xong, số liệu được ghi thẳng vào sheet đã quét; tab <b>Dữ liệu sheet</b> <N>1</N> hiện kết quả. Ô trống là chưa đọc được số liệu, không phải 0.</p><Shot src="08-ket-qua.png" alt="Dữ liệu sheet sau khi quét" /></> },
  { title: 'Xem tab Tổng kết', body: <><p>Mỗi lần quét, ứng dụng cập nhật tab <b>Tổng kết &lt;tên sheet&gt;</b> <N>1</N> trong file: tổng lượt xem, tim, bình luận… của từng đối tác. Bấm tab đó để xem bảng tổng hợp.</p><Shot src="09-tong-ket.png" alt="Tab Tổng kết" /></> },
  { title: 'Xuất báo cáo đối tác', body: <><p>Bấm nút <b>Đối tác &amp; báo cáo</b> <N>1</N> (nút màu cam ở góc trên) để mở bảng <b>Đối tác &amp; báo cáo</b>.</p><Shot src="10a-mo-doi-tac.png" alt="Nút Đối tác và báo cáo" /><p>Chọn sheet ở <b>Chọn sheet</b>, tích các đối tác trong <b>Danh sách đối tác</b> <N>1</N> rồi bấm <b>Xuất báo cáo</b> <N>2</N>. Chọn 1 đối tác được một file .xlsx; nhiều đối tác được một file .zip. <b>Lọc link dưới … view</b> bỏ các link ít view khỏi báo cáo. File được tải về thư mục <b>Tải xuống</b> của trình duyệt; server không lưu lại file báo cáo.</p><Shot src="10-xuat-bao-cao.png" alt="Đối tác và báo cáo" /></> },
  { title: 'Đẩy kết quả lên Google Sheet', adminOnly: true, body: <><p>Ghi lên Google Sheet luôn cần một tài khoản Google. Cách <b>khuyên dùng</b> là <b>tài khoản dịch vụ</b>: trong bảng <b>Nguồn dữ liệu</b> bấm <b>Nạp tài khoản dịch vụ</b> <N>1</N> và chọn file JSON. Cách thay thế là đăng nhập Google cá nhân bằng <b>Nạp OAuth</b> <N>2</N> và nút trạng thái Google <N>3</N>.</p><Shot src="11-google.png" alt="Nạp tài khoản dịch vụ, Nạp OAuth, Đăng nhập Google" /><p>Sau khi nạp, ô xanh hiện email tài khoản dịch vụ. Bấm <b>Copy</b>, mở Google Sheet → <b>Chia sẻ</b>, dán email đó với quyền <b>Người chỉnh sửa</b>. Sau đó chọn <b>Sheet</b> và bấm <b>Tạo sheet</b>: kết quả được ghi vào một tab mới trong Google Sheet ở ô link.</p><Shot src="11b-tai-khoan-dich-vu.png" alt="Email tài khoản dịch vụ, nút Copy và Tạo sheet" /><p><b>Đăng nhập Google:</b> bấm nút trạng thái Google để mở hộp thoại <b>Đăng nhập Google</b>, bấm <b>Mở trang đăng nhập Google</b> <N>1</N> và cho phép quyền. Trình duyệt chuyển tới địa chỉ <code>http://localhost:…</code> và báo không tải được trang, đó là bình thường. Sao chép toàn bộ địa chỉ đó, dán vào ô <N>2</N> rồi bấm <b>Xác nhận</b> <N>3</N>.</p><Shot src="12-google-dan-link.png" alt="Hộp thoại dán địa chỉ localhost" /></> },
  { title: 'Threads', body: <><p>Bấm <b>Threads</b> trên thanh nền tảng <N>1</N>. Threads có nguồn riêng (file, sheet, link); mặc định chế độ là <b>Hybrid</b>, cột LƯỢT LƯU được thay bằng REPOST. Tùy chọn: <b>Cookie Threads</b> <N>2</N> dùng phiên đăng nhập Threads khi quét.</p><Shot src="13-threads.png" alt="Nền tảng Threads" /></> },
  { title: 'Đăng xuất', body: <><p>Bấm <b>Đăng xuất</b> <N>1</N> ở góc trên. Lần sau mở lại cần đăng nhập.</p><Shot src="15-dang-xuat.png" alt="Nút Đăng xuất" /></> },
];

export function GuideDialog() {
  const open = useApp(state => state.modal === 'guide');
  const admin = useApp(state => state.viewer.role === 'admin');
  return (
    <Dialog open={open} onOpenChange={value => !value && closeModal()}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle>Hướng dẫn sử dụng</DialogTitle>
          <DialogDescription>Các bước quét số liệu, xuất báo cáo và đẩy lên Google Sheet.</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <ol className="flex flex-col gap-6">
            {STEPS.filter(step => admin || !step.adminOnly).map((step, index) => (
              <li key={step.title} className="flex gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">{index + 1}</span>
                <div className="min-w-0 flex-1 space-y-2 text-sm leading-relaxed">
                  <h3 className="text-base font-semibold">{step.title}</h3>
                  {step.body}
                </div>
              </li>
            ))}
          </ol>
          <section className="mt-6 rounded-lg border bg-muted/40 p-4 text-sm">
            <h3 className="mb-2 font-semibold">Lưu ý</h3>
            <ul className="list-disc space-y-1 pl-5">
              <li>Mỗi lúc chỉ chạy được một phiên quét cho cả nhóm. Nếu người khác đang quét, hãy chờ phiên đó xong.</li>
              <li>Ô trống nghĩa là chưa biết số liệu, không phải 0. Các dòng tổng chỉ cộng những số đã biết.</li>
              <li>Không đóng tab trong lúc đang xuất báo cáo hoặc tải file.</li>
              {admin && <li>Cookie TikTok: chỉ dùng tài khoản TikTok phụ.</li>}
            </ul>
          </section>
        </DialogBody>
        <DialogFooter><Button variant="outline" onClick={closeModal}>Đóng</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

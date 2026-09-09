# httpmas v3.6.5

Bản cập nhật lớn tập trung vào việc sửa các lỗ hổng chí mạng ở tầng giao thức (HTTP/1.1, TLS, Socket Pool) khiến thư viện bị chặn bởi các WAF khắt khe (như Facebook, Cloudflare). Đồng thời, chuẩn hóa API và khả năng quản lý Cookie để tương thích 100% với thư viện `requests` gốc, hỗ trợ đa nền tảng/đa website.

## Sửa lỗi nghiêm trọng

### 1. Sửa lỗi mất Session Cookie (Multi-value Headers)
- **Vấn đề:** Trước đây, C-Extension (`_httpmas_fast.c`) và Python fallback (`_fast.py`) gộp các header trùng tên bằng dấu phẩy (`, `). Điều này vi phạm RFC 7230 đối với header `Set-Cookie`, khiến CookieJar không thể parse và làm mất hoàn toàn session đăng nhập.
- **Giải pháp:** Thay đổi kiến trúc parser trả về `PyList` chứa các `PyTuple` `(key, value)` thay vì `PyDict`. Lớp `CaseInsensitiveHeaders` giờ đây sử dụng `.add()` để giữ nguyên vẹn mọi `Set-Cookie` riêng biệt.

### 2. Fix lỗi "Giả chết" do SSLWantReadError
- **Vấn đề:** Trong `http_parser.py`, khi đọc dữ liệu qua SSL Socket, nếu OpenSSL chưa giải mã kịp, nó ném ra `SSLWantReadError`. Code cũ bắt lỗi này và trả về `False`, khiến tầng trên tưởng Server đã đóng kết nối (EOF) và ném lỗi `RequestsError`.
- **Giải pháp:** Tích hợp `select.select()` để chờ socket thực sự sẵn sàng đọc rồi thử lại, đảm bảo kết nối SSL ổn định, không bị rớt ngẫu nhiên khi mạng chậm hoặc response lớn.

### 3. Sửa lỗi Logic Domain Matching trong Cookie
- **Vấn đề:** Hàm `matches_domain()` trong `cookies.py` có lỗi logic: nếu cookie không có thuộc tính `Domain` (rỗng), nó so sánh `request_host == ""` và luôn trả về `False`. Điều này khiến cookie không bao giờ được gửi đi.
- **Giải pháp:** Sửa lại theo chuẩn RFC 6265: Nếu `domain` rỗng, mặc định khớp với mọi host (`return True`).

## Bảo mật & Vượt WAF

### 1. Giả lập TLS Fingerprint (JA3) & ALPN
- **Nâng cấp `tls_manager.py`:** 
  - Thêm **ALPN** (`context.set_alpn_protocols(['http/1.1'])`) để tương thích với các WAF yêu cầu giao thức rõ ràng.
  - Cập nhật **Cipher Suite** sắp xếp theo thứ tự của Chrome 112+, giúp giảm thiểu sự khác biệt về vân tay TLS (JA3) so với trình duyệt thật, tránh bị WAF chặn mềm (Soft Block).

### 2. Tối ưu Connection Pool Health Check
- **Vấn đề:** Hàm `_is_healthy()` trong `pool.py` dùng `select()` và hiểu sai dữ liệu TLS pending / Keep-Alive ACK là EOF, dẫn đến việc "giết nhầm" các SSL Socket đang khỏe mạnh, buộc phải tạo kết nối mới (tốn thời gian TLS Handshake).
- **Giải pháp:** Tinh chỉnh logic check cho `SSLSocket`, phân biệt rõ giữa EOF thực sự và trạng thái chờ dữ liệu ứng dụng, giữ lại tối đa connection nóng (hot connections).

## Tính năng mới

### 1. Universal CookieJar (Hỗ trợ mọi Website)
- **Nâng cấp `cookies.py`:** Thêm hàm `update(cookie_dict: dict, url: str)` vào `CookieJar`.
- **Cách dùng:** Cho phép nạp cookie dạng Dictionary (giống hệt `requests`). Hàm này tự động parse `hostname` từ `url` được truyền vào để gán domain, **không hardcode** bất kỳ tên miền nào, giúp `httpmas` hoạt động hoàn hảo trên GitHub, Google, TikTok, Facebook, v.v.
```python
session = httpmas.Session()
cookie_dict = {"c_user": "123", "xs": "abc"}
# Tự động lấy domain từ URL, hỗ trợ MỌI website
session.cookies.update(cookie_dict, url="https://mbasic.facebook.com") 
```

> Lưu ý: Bản cập nhật chưa được đẩy hẳn lên pypi chúng tôi đang test lại trên máy nội bộ sẽ sớm cập nhật ngay trong đêm nay

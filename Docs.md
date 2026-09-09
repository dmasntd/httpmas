# Phiên bản và cập nhật kèm thay đổi

- Phiên bản v1 tức 1.0.0 trở lên
    * Sử dụng tls 1.3 sẵn có
    * Xây trên lõi ssl 
    * Sử tạo api thuần requests để dễ dàng sử dụng
    * Tạo cli cho các lệnh version help info
    * Tạo xử lý parser
 
- Phiên bản v2 tức 2.0 trở lên
    * Thêm dns để resolution
    * Thêm pool 
    * Nâng cao tốc độ xử lý
    * Hỗ trợ kết nối bất đồng bộ
    * Giảm dùng ram và cpu
    * Giảm thiểu tốc độ xử lý và chuyển sang dùng c-types
    * Thay xuống nền socket không dùng ssl
    * Thêm xử lý nếu requests fail sẽ bắn lỗi

- Phiên bản v3 tức 3.0 trở lên
    * Thêm header, cookie, auth để giữ được phiên đăng nhập
    * Thêm lớp tương tác với C để đẩy xuống dùng C xử lý
    * Thay đổi xử lý sang C-Extention
    * Giữ phiên đăng nhập
    * Thêm 1 số xử lý mới
    * Sửa lại kết nối bất đồng bộ để dễ dùng
    * Sửa lại cơ chế bắn lỗi nếu requests fail

- Hiện tại đang ở phiên bản 3.5.9 mới nhất do Nguyễn Tấn Dũng phát hành bản vá đang ổn định cho thời điểm hiện tại, hiện tại chúng tôi vẫn sẽ nâng cấp nếu phát hiện thêm lỗi hoặc chúng tôi có thời gian rảnh
- Không sử dụng phiên bản cũ hoặc các phiên bản do người khác phát hành chúng tôi không khuyến khích điều này vì ảnh hưởng tới chúng tôi rất nhiều
- Chỉ dùng từ phiên bản 3. đổ lên còn trở lại không lên dùng vì rất kém và không xử lý tốt

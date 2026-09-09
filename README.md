# Tác giả : Nguyễn Tấn Dũng & MinhAnhs
 ## ``Form VietNamese``

[![PyPI version](https://img.shields.io/pypi/v/httpmas.svg)](https://pypi.org/project/httpmas/)
[![Python Version](https://img.shields.io/pypi/pyversions/httpmas.svg)](https://pypi.org/project/httpmas/)
[![Downloads](https://img.shields.io/pypi/dm/httpmas.svg)](https://pypi.org/project/httpmas/)
[![Contributors](https://img.shields.io/github/contributors/dmasntd/httpmas.svg)](https://github.com/dmasntd/httpmas/graphs/contributors)
[![Docs](https://img.shields.io/badge/docs-passing-brightgreen.svg)](https://raw.githubusercontent.com/dmasntd/httpmas/refs/heads/main/document.dcm)

----

## Sử dụng
 - Nó được thiết kết quen thuộc với đa số người dùng, các bạn có thể dùng theo ý muốn của mình
 - Nó cũng tương đồng như requests để không cảm thấy khó dùng
 
   ```python
   >>> Sync Requests
   from httpmas import requests
   >>> GET
   r = requests.get("https://httpbin.org/json")
   print(r.status_code)
   print(r.json())
   >>> POST với JSON
   r = requests.post(
      "https://httpbin.org/post",
      json={"name": "httpmas", "version": "1.0"}
   )
   >>> POST form data
   r = requests.post(
       "https://httpbin.org/post",
       data={"username": "admin", "password": "secret"}
   )
   >>> Custom Headers
   r = requests.get(
       "https://api.github.com/repos",
       headers={"Authorization": "Bearer YOUR_TOKEN"}
   )
   >>> Query Parameters
   r = requests.get(
       "https://api.github.com/search/repositories",
        params={"q": "httpmas", "sort": "stars"}
   )
   
   >>> Async 
   from httpmas import asyncio
   async def main():
       r = await asyncio.requests.get("https://httpbin.org/json")
       print(r.json())
    
    # Multiple requests đồng thời
       results = await asyncio.requests.gather(
           asyncio.requests.get("https://httpbin.org/json"),
           asyncio.requests.get("https://httpbin.org/headers"),
           asyncio.requests.get("https://api.github.com/users/github"),
       )
       for r in results:
           print(r.status_code)
    asyncio.run(main())
   
   ```
> Các bạn cũng có thể đọc [Tài liệu hướng dẫn](https://github.com/dmasntd/httpmas/blob/main/H%C6%B0%E1%BB%9BngD%E1%BA%ABn.md) để xem chi tiết hơn


## Cài đặt
 - Hiện tại các bạn có thể cài bằng lệnh sau

   ```bash
   pip install httpmas

   httpmas --version <-- Lệnh kiểm tra phiên bản hiện tại

   httpmas --help <-- Lệnh xem cách dùng

   httpmas --info <-- Lệnh xem thông tin
   ```

### Phát triển bởi tôi
- Thư viện này được tôi "nấu" trên nền socket sẵn có
- Chúng tôi tận dùng socket để giảm tải các tầng nó đi qua tạo ra tốc độ nhanh chóng hơn
- Cũng một phần giải quyết tốc độ cho server của mình

## Báo cáo và thực tế
- Theo phân tích đến từ tác giả MinhAnhs chúng tôi đã so sánh tốc độ cho ra kết quả
  - Nhanh hơn ~3 lần so với requests
  - Nhanh hơn ~2 lần so với aiohttp
- Kết quả này chưa hoàn toàn chuẩn vì còn dựa trên một số yếu điểm thực tế, vả lại nó cũng đang trong giai đoạn phát triển
- Theo 1 số người dùng DEV họ cho thấy
  - Nhỉnh hơn requests 1,7 đến 2 lần
  - Nhỉnh hơn aiohttp 1,8 đến 2,1 
- Thắng aiohttp vì nó cũng chỉ dùng lượng requests bất đồng bộ nhỏ trong khi đó aiohttp thắng ở bất đồng bộ lượng lớn và nó có http2 hoặc thắng được thật(:>)
- Các bạn cũng có thể test và báo cáo tốc độ về cho chúng tôi biết để chúng tôi nâng cấp hơn

## Lưu ý
  - Bản phát hành 2.9.18 này là bản cũ không lên cài thủ công nếu không dùng mục đích nâng cấp phát triển cùng admin
  - Vẫn lên cài bằng ``pip install httpmas`` để sử dụng bản mới nhất
  - Môi trường mạng yếu cũng phải có mức độ nếu yếu quá chúng tôi cũng không thể hỗ trợ
  - Chúng tôi cần thời gian phát triển lên hiện tại chỉ có hỗ trợ http1.1
  - Chúng tôi cũng đang tập trung cho tốc độ và ổn định

# Nâng cấp và cập nhật tình trạng
- Xem tại docs: https://github.com/dmasntd/httpmas/blob/main/Docscument.md


# Liên hệ
- Bạn có thể yêu cầu tham gia dự án hoặc báo cáo hãy nhắn qua telegram [@manhscuti](https://t.me/@manhscuti)

# Tài liệu
- Bạn có thể xem tại đây: https://github.com/dmasntd/httpmas/blob/main/Docs.md

# Đóng góp
- [manhscuti](https://github.com/dma17ntd)

- Rất cảm ơn các bạn đã hỗ trợ 
---

<p align="center">
  <img src="image.png" alt="Image" width="400">
</p>

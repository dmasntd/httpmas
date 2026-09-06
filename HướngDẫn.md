<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-3.5.9-orange.svg)](#)

</div>


# 🚀 httpmas

**Thư viện HTTP client thuần Python, xây dựng hoàn toàn trên socket, không phụ thuộc `requests`/`urllib`/`aiohttp`.**

> ⚡ Nhanh hơn `requests`, mạnh hơn `http.client`, gọn hơn `aiohttp` — tất cả trong một gói duy nhất.

```python
from httpmas import requests

r = requests.get("https://api.github.com/users/github")
print(r.json())
```

---

## 📦 Cài đặt

```bash
pip install httpmas
```

---

## 🚀 Bắt đầu nhanh

### Sync API

```python
from httpmas import requests

# GET request
r = requests.get("https://httpbin.org/json")
print(r.status_code)    # 200
print(r.json())         # {'slideshow': ...}

# POST với JSON
r = requests.post(
    "https://httpbin.org/post",
    json={"name": "httpmas", "version": "1.0"}
)

# POST form data
r = requests.post(
    "https://httpbin.org/post",
    data={"username": "admin", "password": "secret"}
)

# Custom headers
r = requests.get(
    "https://api.github.com/repos",
    headers={"Authorization": "Bearer YOUR_TOKEN"}
)

# Query parameters
r = requests.get(
    "https://api.github.com/search/repositories",
    params={"q": "httpmas", "sort": "stars"}
)
```

### Async API

```python
from httpmas import asyncio

async def main():
    # Single request
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

---

## 🔄 Session

Session duy trì cookies, authentication và headers mặc định giữa các request:

```python
from httpmas import Session

with Session() as s:
    # Login - cookies tự động lưu
    s.post("https://example.com/login", data={
        "username": "admin",
        "password": "secret"
    })
    
    # Request tiếp theo tự động gửi cookie
    r = s.get("https://example.com/profile")
    print(r.json())  # Đã authenticated
```

### Session với Authentication

```python
from httpmas import Session, BasicAuth, BearerAuth

# Basic Auth
with Session(auth=BasicAuth("user", "pass")) as s:
    r = s.get("https://httpbin.org/basic-auth/user/pass")

# Bearer Token
with Session(auth=BearerAuth("your-token-123")) as s:
    r = s.get("https://api.example.com/protected")

# Custom headers cho mọi request
with Session(headers={"User-Agent": "MyApp/1.0"}) as s:
    r = s.get("https://httpbin.org/headers")
```

### Async Session

```python
from httpmas import asyncio

async def main():
    async with asyncio.AsyncSession() as s:
        r = await s.get("https://httpbin.org/cookies/set?name=value")
        r2 = await s.get("https://httpbin.org/cookies")
        print(r2.json())  # Cookie tự động gửi

asyncio.run(main())
```

---

## 🔐 Authentication

### Basic Authentication

```python
from httpmas import requests, BasicAuth

auth = BasicAuth("username", "password")
r = requests.get("https://httpbin.org/basic-auth/user/pass", auth=auth)
```

### Bearer Token

```python
from httpmas import requests, BearerAuth

auth = BearerAuth("your-jwt-token")
r = requests.get("https://api.example.com/protected", auth=auth)
```

### Digest Authentication

```python
from httpmas import Session, DigestAuth

with Session() as s:
    r = s.get(
        "https://httpbin.org/digest-auth/auth/user/pass",
        auth=DigestAuth("user", "pass")
    )
```

### API Key

```python
from httpmas import requests, APIKeyAuth

# API key trong header
auth = APIKeyAuth("X-API-Key", "your-api-key", location="header")
r = requests.get("https://api.example.com/data", auth=auth)

# API key trong query string
auth = APIKeyAuth("api_key", "your-api-key", location="query")
r = requests.get("https://api.example.com/data", auth=auth)
```

---

## 🍪 Cookie Management

### Tự động (với Session)

```python
from httpmas import Session

with Session() as s:
    # Cookie tự động lưu từ Set-Cookie header
    s.get("https://httpbin.org/cookies/set?session_id=abc123")
    
    # Cookie tự động gửi trong request tiếp theo
    r = s.get("https://httpbin.org/cookies")
    print(r.json())  # {'cookies': {'session_id': 'abc123'}}
```

### Thủ công (với CookieJar)

```python
from httpmas import requests, CookieJar, Cookie

jar = CookieJar()

# Thêm cookie thủ công
jar.set_cookie(Cookie(
    name="session_id",
    value="abc123",
    domain="example.com",
    path="/",
    secure=True,
    http_only=True,
))

# Xóa cookie
jar.clear(domain="example.com", name="session_id")

# Xóa tất cả
jar.clear_all()

# Đếm số cookie
print(len(jar))
```

### Cookie Policy

```python
from httpmas import Session, CookieJar, CookiePolicy

# Chặn cookie từ một số domain
policy = CookiePolicy(
    accept_all=True,
    blocked_domains=["tracking.com", "analytics.net"],
    max_cookies=100,
)
jar = CookieJar(policy=policy)

with Session(cookies=jar) as s:
    r = s.get("https://example.com")
```

---

## ↪️ Redirect Handling

### Tự động follow (mặc định)

```python
from httpmas import requests

r = requests.get("https://httpbin.org/redirect/3")
print(r.status_code)   # 200
print(r.url)           # URL cuối cùng
print(len(r.history))  # 3 (số lần redirect)
```

### Disable redirect

```python
r = requests.get(
    "https://httpbin.org/redirect/3",
    allow_redirects=False
)
print(r.status_code)   # 302
print(r.headers.get("location"))
```

### Giới hạn số lần redirect

```python
from httpmas import Session

with Session(max_redirects=5) as s:
    r = s.get("https://httpbin.org/redirect/10")  # Sẽ raise error sau 5 lần
```

---

## 🛡️ Error Handling

### Mặc định: tự động raise khi server trả 4xx/5xx

```python
from httpmas import requests, HTTPError, RequestsError

try:
    r = requests.get("https://httpbin.org/status/404")
    print(r.json())
except HTTPError as e:
    # Server trả lỗi HTTP (404, 500, ...)
    print(f"Server lỗi: {e.status_code}")
    print(f"Response: {e.response.text}")
except RequestsError as e:
    # Lỗi mạng (timeout, DNS, TLS, ...)
    print(f"Lỗi mạng: {e}")
```

### Disable auto-raise

```python
# Tự kiểm tra status code
r = requests.get("https://httpbin.org/status/404", raise_on_error=False)
if r.status_code == 200:
    print(r.json())
elif r.status_code == 404:
    print("Không tìm thấy")
```

### Timeout

```python
# Timeout cho toàn bộ request
r = requests.get("https://httpbin.org/delay/5", timeout=3.0)
# Sẽ raise RequestsError sau 3 giây
```

---

## 📊 Response Object

```python
r = requests.get("https://httpbin.org/json")

# Status
print(r.status_code)    # 200
print(r.reason)         # "OK"
print(r.ok)             # True
print(r.is_redirect)    # False

# Headers
print(r.headers["content-type"])
print(r.headers.get("x-custom", "default"))

# Body
print(r.content)        # bytes
print(r.text)           # str (auto-decode)
print(r.json())         # dict/list

# Metadata
print(r.url)            # URL cuối cùng
print(r.elapsed)        # Thời gian (giây)
print(r.encoding)       # Encoding phát hiện
print(r.history)        # Danh sách Response trước (redirects)
```

---

## 🖥️ CLI Tool

Sau khi cài đặt, dùng lệnh `httpmas` từ terminal:

```bash
# Xem phiên bản và banner
httpmas --version

# Xem trợ giúp
httpmas --help

# Thông tin hệ thống
httpmas --info
```

---

## 🔧 Advanced Usage

### Custom Headers

```python
from httpmas import Session, CaseInsensitiveHeaders

headers = CaseInsensitiveHeaders({
    "User-Agent": "MyApp/1.0",
    "Accept": "application/json",
    "X-Custom-Header": "value",
})

with Session(headers=headers) as s:
    r = s.get("https://httpbin.org/headers")
```

### Manual RequestManager

```python
from httpmas import RequestManager

# Tạo manager với timeout và retry tùy chỉnh
mgr = RequestManager(timeout=15.0, max_retries=3)

r = mgr.get("https://httpbin.org/json")
print(r.json())

mgr.close()
```

### AsyncRequestManager

```python
from httpmas import AsyncRequestManager

async def main():
    mgr = AsyncRequestManager(timeout=10.0)
    r = await mgr.async_get("https://httpbin.org/json")
    print(r.json())
    await mgr.close()

import asyncio
asyncio.run(main())
```

---

## 📈 Benchmark

Chạy benchmark so sánh với các thư viện khác:

```bash
python benchmark.py
```

**Kết quả điển hình (10 requests, 5 rounds):**

| Thư viện | p50 | p95 | req/s |
|----------|-----|-----|-------|
| **httpmas** | ~80ms | ~120ms | ~12.5 |
| requests | ~90ms | ~150ms | ~11.1 |
| httpx | ~85ms | ~140ms | ~11.8 |
| aiohttp | ~75ms | ~110ms | ~13.3 |

> 💡 Benchmark có thể khác nhau tùy điều kiện mạng và máy chủ.

---

## 🐛 Troubleshooting

### Lỗi TLS/SSL

```python
# Reset SSL context nếu cần
from httpmas.tls_manager import TLSManager
TLSManager.reset_context()
```

### Xóa DNS cache

```python
from httpmas.dns import DNSCache
DNSCache().clear()
```

### Android/Termux warning

Nếu thấy warning `library not accessible for the namespace`, đó là lỗi từ Android linker. Thư viện vẫn hoạt động bình thường với Python fallback.

---

## 🤝 Đóng góp

1. Fork repository
2. Tạo branch mới (`git checkout -b feature/amazing`)
3. Commit thay đổi (`git commit -m 'Add amazing feature'`)
4. Push lên branch (`git push origin feature/amazing`)
5. Mở Pull Request

---

## 📄 License

MIT License - Xem file [LICENSE](LICENSE) để biết chi tiết.

---

## 👤 Tác giả

**PyMaKaizu** - [𝑫𝒎𝒂𝒔](https://github.com/dmasntd)

---

## 🙏 Cảm ơn

- [httpbin.org](https://httpbin.org) - HTTP testing service
- [Python](https://python.org) - Ngôn ngữ lập trình tuyệt vời
- Cộng đồng open source

---

<div align="center">

**Made with ❤️ in Vietnam**

[⬆ Back to top](#httpmas)

</div>

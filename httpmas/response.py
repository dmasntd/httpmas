"""
Đối tượng Response của httpmas.
Đóng gói toàn bộ thông tin phản hồi HTTP.
Nâng cấp: thêm cookies, history, final_url.
"""
import json as _json
from typing import Any, Dict, List, Optional

from .exceptions import RequestsError


class Response:
    """Đối tượng phản hồi HTTP.

    Thuộc tính:
        status_code: Mã trạng thái HTTP.
        reason: Lý do (ví dụ: "OK", "Not Found").
        headers: Từ điển headers phản hồi (CaseInsensitiveHeaders).
        content: Body dạng byte thô.
        url: URL đã request (URL cuối cùng sau redirect).
        encoding: Mã hóa ký tự được phát hiện.
        elapsed: Thời gian xử lý request (giây).
        history: Danh sách Response trung gian (redirect chain).
        cookies: Danh sách Cookie từ response này.
    """

    __slots__ = (
        "status_code", "reason", "headers", "content",
        "url", "elapsed", "encoding",
        "_history", "_cookies",
    )

    def __init__(
        self,
        status_code: int,
        reason: str,
        headers: Dict[str, str],
        content: bytes,
        url: str,
        elapsed: float = 0.0,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.headers = headers
        self.content = content
        self.url = url
        self.elapsed = elapsed
        self.encoding: Optional[str] = None
        self._history: List["Response"] = []
        self._cookies: List[Any] = []

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    @property
    def history(self) -> List["Response"]:
        return list(self._history)

    @property
    def cookies(self) -> List[Any]:
        return list(self._cookies)

    @property
    def final_url(self) -> str:
        return self.url

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    @property
    def is_permanent_redirect(self) -> bool:
        return self.status_code in (301, 308)

    @property
    def text(self) -> str:
        encoding = self._detect_encoding()
        try:
            return self.content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            return self.content.decode("utf-8", errors="replace")

    def _detect_encoding(self) -> str:
        if self.encoding:
            return self.encoding

        content_type = ""
        if hasattr(self.headers, "get"):
            content_type = self.headers.get("content-type", "")
        elif isinstance(self.headers, dict):
            content_type = self.headers.get("content-type", "")

        if "charset=" in content_type:
            parts = content_type.split("charset=")
            if len(parts) > 1:
                charset = parts[1].split(";")[0].strip().strip("'\"")
                if charset:
                    return charset

        if self.content.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if self.content.startswith(b"\xff\xfe"):
            return "utf-16-le"
        if self.content.startswith(b"\xfe\xff"):
            return "utf-16-be"

        return "utf-8"

    def json(self) -> Any:
        try:
            return _json.loads(self.text)
        except _json.JSONDecodeError as exc:
            raise RequestsError(f"Không thể phân tích JSON: {exc}")

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise RequestsError(
                f"HTTP {self.status_code} {self.reason}"
            )

    def __repr__(self) -> str:
        return f"<Response [{self.status_code} {self.reason}]>"

    def __bool__(self) -> bool:
        return self.ok
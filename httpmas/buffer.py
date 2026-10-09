from typing import Optional


class ByteBuffer:
    """Buffer byte tối ưu, an toàn với resize."""

    __slots__ = ("_buf", "_offset")

    def __init__(self) -> None:
        self._buf = bytearray()
        self._offset = 0

    def __len__(self) -> int:
        return len(self._buf) - self._offset

    def extend(self, data: bytes) -> None:
        """Thêm dữ liệu vào buffer."""
        if not data:
            return
        self._buf.extend(data)

    def find(self, sub: bytes) -> int:
        """Tìm vị trí tương đối của sub trong buffer."""
        idx = self._buf.find(sub, self._offset)
        if idx < 0:
            return -1
        return idx - self._offset

    def read(self, count: Optional[int] = None) -> bytes:
        """Đọc và tiêu thụ dữ liệu."""
        if count is None:
            end = len(self._buf)
        else:
            end = min(self._offset + count, len(self._buf))

        data = bytes(self._buf[self._offset:end])
        self._offset = end
        self._maybe_compact()
        return data

    def read_until(self, sep: bytes) -> Optional[bytes]:
        """Đọc tới separator, không bao gồm separator.

        Trả về:
            Bytes trước separator, hoặc None nếu chưa có separator.
        """
        idx = self.find(sep)
        if idx < 0:
            return None

        start = self._offset
        end = start + idx
        data = bytes(self._buf[start:end])
        self._offset = end + len(sep)
        self._maybe_compact()
        return data

    def _maybe_compact(self) -> None:
        """Dọn buffer khi offset quá lớn."""
        if self._offset > 8192 and self._offset * 2 > len(self._buf):
            self._buf = self._buf[self._offset:]
            self._offset = 0

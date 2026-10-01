"""
Copyright by MinhAnhs aka manhscuti
"""
try:
    from ._httpmas_fast import (
        find_header_end,
        parse_response_head,
        parse_headers_raw,
        urlencode_value,
        parse_chunk_size,
    )
    C_ACCELERATION = True
except (ImportError, OSError, Exception):
    C_ACCELERATION = False

    def find_header_end(buf, start, end):
        if end < 0:
            end = 0
        return buf.find(b"\r\n\r\n", start, end)

    def parse_response_head(head_bytes):
        lines = head_bytes.split(b"\r\n")
        if not lines or not lines[0]:
            raise ValueError("Status line không hợp lệ")
        first = lines[0]
        if not first.startswith(b"HTTP/"):
            raise ValueError("Invalid HTTP version prefix")
        parts = first.split(b" ", 2)
        if len(parts) < 2:
            raise ValueError("Status line không hợp lệ")
        try:
            http_version = parts[0].decode("ascii", "replace").upper()
            status_code = int(parts[1])
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError(f"Status line không hợp lệ: {e}")
        if status_code < 100 or status_code > 599:
            raise ValueError("Status code ngoài khoảng 100-599")
        reason = parts[2].decode("latin-1", "replace") if len(parts) > 2 else ""
        headers = {}
        for line in lines[1:]:
            if not line:
                continue
            key, sep, value = line.partition(b":")
            if not sep:
                continue
            try:
                k = key.strip().decode("latin-1").lower()
                v = value.strip().decode("latin-1")
            except UnicodeDecodeError:
                k = key.strip().decode("utf-8", "replace").lower()
                v = value.strip().decode("utf-8", "replace")
            if not k:
                continue
            if k in headers:
                headers[k] = headers[k] + ", " + v
            else:
                headers[k] = v
        return status_code, reason, http_version, headers

    def parse_headers_raw(buf, start, end):
        """Fallback: tìm header end rồi gọi parse_response_head."""
        if end < 0:
            end = 0
        idx = buf.find(b"\r\n\r\n", start, end)
        if idx < 0:
            raise ValueError("Không tìm thấy header end")
        head_bytes = bytes(buf[start:idx])
        status, reason, version, headers = parse_response_head(head_bytes)
        body_offset = idx + 4
        return status, reason, version, headers, body_offset

    def urlencode_value(value):
        _SAFE = frozenset(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-.~"
        )
        result = []
        for byte in value.encode("utf-8"):
            char = chr(byte)
            if char in _SAFE:
                result.append(char)
            elif char == " ":
                result.append("+")
            else:
                result.append(f"%{byte:02X}")
        return "".join(result)

    def parse_chunk_size(line):
        if len(line) > 8192:
            raise ValueError("Chunk line too long")
        size_str = line.decode("ascii").split(";")[0].strip()
        return int(size_str, 16)


def urlencode_dict(data):
    pairs = []
    for key, value in data.items():
        pairs.append(
            f"{urlencode_value(str(key))}={urlencode_value(str(value))}"
        )
    return "&".join(pairs)

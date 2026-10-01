"""
Hệ thống xử lý lỗi của httpmas.
Cung cấp RequestsError với hiển thị màu RGB trên terminal.
Hỗ trợ print_error=False cho retry silent.
"""

import sys
import threading


class _ColorPrinter:
    """In thông báo lỗi với mã màu RGB TrueColor ra stderr."""

    WHITE = "\033[38;2;255;255;255m"
    BLUE = "\033[38;2;120;180;255m"
    RED = "\033[38;2;255;50;50m"
    ORANGE = "\033[38;2;255;170;50m"
    RESET = "\033[0m"

    _lock = threading.Lock()

    @classmethod
    def print_error(cls, message: str) -> None:
        """In lỗi ra stderr ngay lập tức, an toàn với đa luồng."""
        formatted = (
            f"{cls.WHITE}[{cls.BLUE}Dmas{cls.WHITE}] "
            f"{cls.RED}RequestsError{cls.WHITE}: "
            f"{cls.ORANGE}{message}{cls.RESET}"
        )
        with cls._lock:
            sys.stderr.write(formatted + "\n")
            sys.stderr.flush()


class RequestsError(Exception):
    """Ngoại lệ tùy chỉnh cho httpmas.

    Có thể tắt in lỗi ngay bằng print_error=False để dùng
    trong các vòng retry mạng yếu, tránh spam log.
    """

    __slots__ = ("message", "print_error")

    def __init__(self, message: str, print_error: bool = True) -> None:
        self.message = str(message)
        self.print_error = print_error
        if print_error:
            _ColorPrinter.print_error(self.message)
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"RequestsError: {self.message}"
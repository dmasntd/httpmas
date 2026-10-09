import sys
import threading


class _ColorPrinter:
    WHITE = "\033[38;2;255;255;255m"
    BLUE = "\033[38;2;120;180;255m"
    RED = "\033[38;2;255;50;50m"
    ORANGE = "\033[38;2;255;170;50m"
    YELLOW = "\033[38;2;255;255;0m"
    RESET = "\033[0m"
    _lock = threading.Lock()

    @classmethod
    def print_error(cls, message: str) -> None:
        formatted = (
            f"{cls.WHITE}[{cls.BLUE}Dmas{cls.WHITE}] "
            f"{cls.RED}RequestsError{cls.WHITE}: "
            f"{cls.ORANGE}{message}{cls.RESET}"
        )
        with cls._lock:
            sys.stderr.write(formatted + "\n")
            sys.stderr.flush()

    @classmethod
    def print_http_error(cls, message: str, status_code: int) -> None:
        """In lỗi HTTP với màu theo status code."""
        if status_code >= 500:
            color = cls.RED
        elif status_code >= 400:
            color = cls.ORANGE
        else:
            color = cls.YELLOW
        formatted = (
            f"{cls.WHITE}[{cls.BLUE}Dmas{cls.WHITE}] "
            f"{color}HTTP {status_code}{cls.WHITE}: "
            f"{cls.ORANGE}{message}{cls.RESET}"
        )
        with cls._lock:
            sys.stderr.write(formatted + "\n")
            sys.stderr.flush()


class RequestsError(Exception):
    """Ngoại lệ cơ sở cho httpmas.

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


class HTTPError(RequestsError):
    """Lỗi HTTP do server trả về (status >= 400).

    Cách dùng:
        from httpmas import requests, HTTPError

        try:
            r = requests.get("https://example.com/api")
        except HTTPError as e:
            print(f"Server lỗi: {e.status_code}")
            print(e.response.text)
        except RequestsError as e:
            print(f"Lỗi mạng: {e}")
    """
    __slots__ = ("status_code", "response")

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        response=None,
        print_error: bool = True,
    ) -> None:
        self.status_code = status_code
        self.response = response
        if print_error and status_code > 0:
            _ColorPrinter.print_http_error(message, status_code)
            # Gọi Exception.__init__ để tránh in lỗi 2 lần
            Exception.__init__(self, message)
            self.message = str(message)
            self.print_error = False
        else:
            super().__init__(message, print_error=print_error)

    def __str__(self) -> str:
        if self.status_code > 0:
            return f"HTTPError {self.status_code}: {self.message}"
        return f"HTTPError: {self.message}"
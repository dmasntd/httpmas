"""
Quản lý TLS/SSL cho httpmas.
FIX CRITICAL: Loại bỏ hidden reconnect bypass timeout.
"""
import ssl
import socket
import threading
from typing import Optional, Dict
from .exceptions import RequestsError

class TLSManager:
    __slots__ = ()
    _context: Optional[ssl.SSLContext] = None
    _context_lock = threading.Lock()
    _session_cache: Dict[str, ssl.SSLSession] = {}
    _session_lock = threading.Lock()
    _MAX_SESSIONS = 128

    @classmethod
    def _create_context(cls) -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:!aNULL:!MD5:!DSS")
        except ssl.SSLError: pass
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        try:
            mode = getattr(ssl, "SESS_CACHE_CLIENT", None)
            if mode is not None and hasattr(context, "set_session_cache_mode"):
                context.set_session_cache_mode(mode)
        except Exception: pass
        try:
            if hasattr(context, "options"):
                context.options |= getattr(ssl, "OP_NO_RENEGOTIATION", 0)
        except Exception: pass
        return context

    @classmethod
    def get_context(cls) -> ssl.SSLContext:
        if cls._context is None:
            with cls._context_lock:
                if cls._context is None:
                    try: cls._context = cls._create_context()
                    except Exception as exc:
                        raise RequestsError(f"Không thể khởi tạo TLS context: {exc}", print_error=False)
        return cls._context

    @classmethod
    def _get_cached_session(cls, hostname: str) -> Optional[ssl.SSLSession]:
        with cls._session_lock: return cls._session_cache.get(hostname)

    @classmethod
    def _store_session(cls, hostname: str, session) -> None:
        if session is None: return
        with cls._session_lock:
            if len(cls._session_cache) >= cls._MAX_SESSIONS:
                try:
                    oldest = next(iter(cls._session_cache))
                    cls._session_cache.pop(oldest, None)
                except StopIteration: pass
            cls._session_cache[hostname] = session

    @classmethod
    def wrap_socket(cls, sock: socket.socket, hostname: str) -> ssl.SSLSocket:
        context = cls.get_context()
        cached_session = cls._get_cached_session(hostname)
        try:
            if cached_session is not None:
                try:
                    ssl_sock = context.wrap_socket(sock, server_hostname=hostname, session=cached_session)
                except TypeError:
                    ssl_sock = context.wrap_socket(sock, server_hostname=hostname)
            else:
                ssl_sock = context.wrap_socket(sock, server_hostname=hostname)
            
            try:
                new_session = ssl_sock.session
                if new_session is not None: cls._store_session(hostname, new_session)
            except (AttributeError, TypeError): pass
            return ssl_sock
            
        except ssl.SSLCertVerificationError as exc:
            cls._safe_close(sock)
            raise RequestsError(f"Xác minh chứng chỉ thất bại cho {hostname}: {exc}", print_error=False)
        except ssl.SSLError as exc:
            # FIX: XÓA hidden reconnect. Chỉ xóa cache và ném lỗi để Engine retry sạch sẽ.
            if cached_session is not None:
                with cls._session_lock: cls._session_cache.pop(hostname, None)
            cls._safe_close(sock)
            raise RequestsError(f"Lỗi TLS khi kết nối {hostname}: {exc}", print_error=False)
        except OSError as exc:
            cls._safe_close(sock)
            raise RequestsError(f"Lỗi kết nối TLS tới {hostname}: {exc}", print_error=False)

    @staticmethod
    def _safe_close(sock: Optional[socket.socket]) -> None:
        if sock is None: return
        try: sock.close()
        except OSError: pass

    @classmethod
    def reset_context(cls) -> None:
        with cls._context_lock: cls._context = None
        with cls._session_lock: cls._session_cache.clear()
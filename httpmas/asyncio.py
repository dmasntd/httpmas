"""
httpmas.asyncio - Async HTTP client API.

Cách dùng:
    from httpmas import asyncio

    async def main():
        r = await asyncio.requests.get("https://example.com")
        print(r.status_code, r.json())

    asyncio.run(main())
"""
import asyncio as _stdlib_asyncio
from asyncio import (
    run,
    sleep,
    wait,
    wait_for,
    gather,
    create_task,
    ensure_future,
    get_event_loop,
    get_running_loop,
    new_event_loop,
    set_event_loop,
    shield,
    as_completed,
    FIRST_COMPLETED,
    FIRST_EXCEPTION,
    ALL_COMPLETED,
    Queue,
    Lock,
    Event,
    Semaphore,
    Condition,
    BoundedSemaphore,
    Barrier,
    TimeoutError,
    CancelledError,
    InvalidStateError,
    IncompleteReadError,
    LimitOverrunError,
)


class _AsyncRequests:
    """Async HTTP requests API."""

    async def request(self, method, url, **kwargs):
        from .requests import async_request
        return await async_request(method, url, **kwargs)

    async def get(self, url, **kwargs):
        from .requests import async_get
        return await async_get(url, **kwargs)

    async def post(self, url, **kwargs):
        from .requests import async_post
        return await async_post(url, **kwargs)

    async def put(self, url, **kwargs):
        from .requests import async_put
        return await async_put(url, **kwargs)

    async def delete(self, url, **kwargs):
        from .requests import async_delete
        return await async_delete(url, **kwargs)

    async def patch(self, url, **kwargs):
        from .requests import async_patch
        return await async_patch(url, **kwargs)

    async def head(self, url, **kwargs):
        from .requests import _get_async_manager
        mgr = _get_async_manager()
        return await mgr._request("HEAD", url, **kwargs)

    async def options(self, url, **kwargs):
        from .requests import _get_async_manager
        mgr = _get_async_manager()
        return await mgr._request("OPTIONS", url, **kwargs)


requests = _AsyncRequests()


class AsyncSession:
    """Async Session alias."""
    def __new__(cls, *args, **kwargs):
        from .session import AsyncSession as _AsyncSession
        return _AsyncSession(*args, **kwargs)


class Session:
    """Sync Session alias."""
    def __new__(cls, *args, **kwargs):
        from .session import Session as _Session
        return _Session(*args, **kwargs)

from __future__ import annotations

import asyncio

from fastapi import WebSocket


class ProcessingConnectionLimiter:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> bool:
        async with self._lock:
            if len(self._connections) >= self.maximum:
                return False
            self._connections.add(websocket)
            return True

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    @property
    def active_count(self) -> int:
        return len(self._connections)


async def wait_for_disconnect(websocket: WebSocket, timeout: float) -> bool:
    """Wait for a client disconnect without changing the update cadence."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while (remaining := deadline - loop.time()) > 0:
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=remaining)
        except TimeoutError:
            return False
        if message["type"] == "websocket.disconnect":
            return True
    return False

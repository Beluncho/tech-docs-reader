from __future__ import annotations

import asyncio
from concurrent.futures import Executor
from typing import Any, Callable


class BusyError(RuntimeError):
    pass


class ImmediateCapacityGate:
    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._in_use = 0

    @property
    def in_use(self) -> int:
        return self._in_use

    def try_acquire(self) -> bool:
        if self._in_use >= self._capacity:
            return False
        self._in_use += 1
        return True

    def release(self) -> None:
        if self._in_use <= 0:
            raise RuntimeError("capacity gate released without an owner")
        self._in_use -= 1


class PoolDispatcher:
    def __init__(self, executor: Executor, *, max_concurrency: int) -> None:
        self._executor = executor
        self._gate = ImmediateCapacityGate(max_concurrency)

    @property
    def in_use(self) -> int:
        return self._gate.in_use

    async def run(self, function: Callable[..., Any], *args: Any) -> Any:
        if not self._gate.try_acquire():
            raise BusyError("all OCR processes are occupied")

        loop = asyncio.get_running_loop()
        try:
            process_future = self._executor.submit(function, *args)
        except Exception:
            self._gate.release()
            raise

        def release_capacity(_future: Any) -> None:
            loop.call_soon_threadsafe(self._gate.release)

        process_future.add_done_callback(release_capacity)
        async_future = asyncio.wrap_future(process_future, loop=loop)
        return await asyncio.shield(async_future)

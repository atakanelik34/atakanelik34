import asyncio
import json
import time

from idp.infrastructure.queue.presence import PresencePublisher
from idp.infrastructure.queue.redis import HEARTBEAT_KEY_PREFIX, WorkerHeartbeat


class FakeRedis:
    def __init__(self) -> None:
        self.writes: list[float] = []
        self.deleted: list[str] = []

    async def set(self, key: str, value: str, ex: int) -> None:
        self.writes.append(json.loads(value)["last_seen_at"])

    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def test_heartbeat_keeps_running_while_job_slots_are_busy() -> None:
    redis = FakeRedis()
    now = time.time()
    beat = WorkerHeartbeat("w:1", "w", 1, "0.1.0", 1, now, now)
    publisher = PresencePublisher(redis, beat, interval_seconds=0.02)  # type: ignore[arg-type]
    await publisher.start()

    async def long_job() -> None:  # occupies the only "slot" for many intervals
        await asyncio.sleep(0.3)

    await long_job()
    await publisher.stop()

    assert len(redis.writes) >= 8
    assert redis.writes == sorted(redis.writes)
    assert redis.deleted == [HEARTBEAT_KEY_PREFIX + "w:1"]


async def test_heartbeat_survives_redis_errors() -> None:
    class FlakyRedis(FakeRedis):
        calls = 0

        async def set(self, key: str, value: str, ex: int) -> None:
            self.calls += 1
            if self.calls == 2:
                raise ConnectionError("blip")
            await super().set(key, value, ex)

    redis = FlakyRedis()
    now = time.time()
    publisher = PresencePublisher(
        redis,  # type: ignore[arg-type]
        WorkerHeartbeat("w:2", "w", 2, "0.1.0", 1, now, now),
        interval_seconds=0.01,
    )
    await publisher.start()
    await asyncio.sleep(0.1)
    await publisher.stop()
    assert len(redis.writes) >= 3

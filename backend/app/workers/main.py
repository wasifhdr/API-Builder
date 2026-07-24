import asyncio
import json
import logging
import sys

from redis.asyncio import Redis

from app.config import settings
from app.redis import redis_client
from app.workers import handlers
from app.workers.periodic import periodic_sweep

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")

QUEUES = {  # stream -> (handler, max concurrent)
    "jobs:rec": (handlers.record_session, settings.rec_max_concurrency),
    "jobs:exec": (handlers.execute_api, settings.exec_max_concurrency),
    # LLM job concurrency pinned to 1 (project rule): serialize generations so
    # the single hosted-model quota isn't fanned out across parallel requests.
    "jobs:llm": (handlers.generate_spec, 1),
}


async def consume(redis: Redis, stream: str, handler, limit: int) -> None:
    group = "workers"
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:  # BUSYGROUP on restart
        pass
    sem = asyncio.Semaphore(limit)

    async def run(msg_id: str, fields: dict) -> None:
        try:
            await handler(json.loads(fields["payload"]))
        except Exception:
            log.exception("job failed stream=%s id=%s", stream, msg_id)
        finally:
            await redis.xack(stream, group, msg_id)
            sem.release()

    while True:
        await sem.acquire()  # backpressure before reading
        try:
            resp = await redis.xreadgroup(group, f"c-{stream}", {stream: ">"}, count=1, block=5000)
        except Exception:
            # A blocking read can hit a transient timeout/connection hiccup;
            # one queue's blip shouldn't take down the whole worker process.
            log.exception("xreadgroup failed stream=%s, retrying", stream)
            sem.release()
            await asyncio.sleep(1)
            continue
        if not resp:
            sem.release()
            continue
        _, messages = resp[0]
        msg_id, fields = messages[0]
        asyncio.create_task(run(msg_id, fields))


async def main() -> None:
    log.info("worker starting, queues=%s", list(QUEUES.keys()))
    await asyncio.gather(
        *(consume(redis_client, s, h, n) for s, (h, n) in QUEUES.items()),
        periodic_sweep(),
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        # Playwright spawns subprocesses; requires the (default) Proactor loop.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())

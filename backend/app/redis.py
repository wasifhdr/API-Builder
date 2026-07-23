from redis.asyncio import Redis

from app.config import settings

# socket_keepalive + health_check_interval: on this dev setup (Docker Desktop
# port-forwarding), long-idle connections in the pool can get silently dropped
# by the network layer; without these, the next command on that connection
# hangs until it hits its own read timeout instead of failing/reconnecting
# immediately. Matters most for the worker's long-lived blocking XREADGROUP.
# socket_timeout MUST exceed the worker's blocking XREADGROUP window (block=5000
# → 5s in workers/main.py). redis-py derives a 5s read timeout from
# health_check_interval; equal to the 5s block, it fires at the exact boundary on
# every idle queue, raising a spurious "Timeout reading" and — on shared pooled
# connections — killing the recorder's heartbeat mid-session. 10s gives the 5s
# block ample headroom while still detecting a genuinely dead connection.
redis_client = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_keepalive=True,
    health_check_interval=30,
    socket_timeout=10,
)

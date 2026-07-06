from redis.asyncio import Redis

from app.config import settings

# socket_keepalive + health_check_interval: on this dev setup (Docker Desktop
# port-forwarding), long-idle connections in the pool can get silently dropped
# by the network layer; without these, the next command on that connection
# hangs until it hits its own read timeout instead of failing/reconnecting
# immediately. Matters most for the worker's long-lived blocking XREADGROUP.
redis_client = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_keepalive=True,
    health_check_interval=30,
)

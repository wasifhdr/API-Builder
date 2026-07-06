from redis.asyncio import Redis

LOGIN_MAX_FAILED_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60


def _login_fail_key(email: str) -> str:
    return f"loginfail:{email.strip().lower()}"


async def too_many_failed_logins(redis: Redis, email: str) -> bool:
    count = await redis.get(_login_fail_key(email))
    return count is not None and int(count) >= LOGIN_MAX_FAILED_ATTEMPTS


async def record_failed_login(redis: Redis, email: str) -> None:
    key = _login_fail_key(email)
    new_count = await redis.incr(key)
    if new_count == 1:
        await redis.expire(key, LOGIN_WINDOW_SECONDS)


async def reset_failed_logins(redis: Redis, email: str) -> None:
    await redis.delete(_login_fail_key(email))

import re

USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,30}$")
USERNAME_HINT = "username must be 3-30 characters: lowercase letters, numbers, and underscores"


def normalize_username(raw: str) -> str:
    return raw.strip().lower()


def is_valid_username(username: str) -> bool:
    return bool(USERNAME_PATTERN.fullmatch(username))

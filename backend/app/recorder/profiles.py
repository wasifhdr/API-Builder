import tempfile
import uuid
from pathlib import Path

from app.config import settings


def get_profile_dir(user_id: uuid.UUID, use_saved_logins: bool) -> tuple[Path, bool]:
    """Returns (profile_dir, is_temporary).

    When use_saved_logins is on, the recorder reuses a persistent, app-managed
    profile per user (so logins survive across sessions). Otherwise it gets a
    throwaway directory that the caller should delete after the session ends.
    """
    if use_saved_logins:
        profile_dir = settings.profiles_path / str(user_id)
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir, False

    return Path(tempfile.mkdtemp(prefix="ab-recorder-")), True

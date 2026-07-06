from app.recorder.session import RecordingSession


async def record_session(payload: dict) -> None:
    await RecordingSession(payload["workflow_id"], payload["user_id"]).run()

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

from app.config import settings
from app.core.security import decrypt_bytes
from app.db import async_session
from app.models.api import CustomApi
from app.models.execution import ApiExecution, ExecutionStatus
from app.models.workflow import Workflow
from app.recorder.replay import ReplayError, replay_workflow
from app.recorder.session import RecordingSession
from app.redis import redis_client


async def record_session(payload: dict) -> None:
    await RecordingSession(payload["workflow_id"], payload["user_id"]).run()


async def execute_api(payload: dict) -> None:
    execution_id = uuid.UUID(payload["execution_id"])
    api_id = uuid.UUID(payload["api_id"])
    params = payload.get("params", {})

    started = time.monotonic()

    async with async_session() as db:
        execution = await db.get(ApiExecution, execution_id)
        api = await db.get(CustomApi, api_id)
        if execution is None or api is None:
            return

        workflow = await db.get(Workflow, api.workflow_id)
        storage_state = None
        if workflow is not None and workflow.auth_state_encrypted is not None:
            try:
                storage_state = json.loads(decrypt_bytes(workflow.auth_state_encrypted))
            except Exception:
                storage_state = None

        execution.status = ExecutionStatus.RUNNING
        execution.started_at = datetime.now(timezone.utc)
        await db.commit()

        workflow_snapshot = api.workflow_snapshot

    try:
        replay_result = await asyncio.wait_for(
            replay_workflow(workflow_snapshot, params, storage_state, execution_id),
            timeout=settings.exec_timeout_seconds,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        result_payload = {"status": "succeeded", "data": replay_result["data"], "duration_ms": duration_ms}
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - started) * 1000)
        result_payload = {"status": "timeout", "error": "execution timed out", "duration_ms": duration_ms}
    except ReplayError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        result_payload = {
            "status": "failed",
            "error": str(exc),
            "duration_ms": duration_ms,
            "artifact_path": exc.artifact_path,
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        result_payload = {"status": "failed", "error": str(exc), "duration_ms": duration_ms}

    async with async_session() as db:
        execution = await db.get(ApiExecution, execution_id)
        if execution is not None:
            execution.finished_at = datetime.now(timezone.utc)
            execution.duration_ms = result_payload["duration_ms"]
            if result_payload["status"] == "succeeded":
                execution.status = ExecutionStatus.SUCCEEDED
                execution.result = result_payload["data"]
            elif result_payload["status"] == "timeout":
                execution.status = ExecutionStatus.TIMEOUT
                execution.error_message = result_payload["error"]
            else:
                execution.status = ExecutionStatus.FAILED
                execution.error_message = result_payload["error"]
                execution.failure_artifact_path = result_payload.get("artifact_path")
            await db.commit()

    await redis_client.set(f"exec:result:{execution_id}", json.dumps(result_payload), ex=600)
    await redis_client.publish(f"exec:done:{execution_id}", "done")

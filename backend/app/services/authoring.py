"""Shared plumbing for out-of-session parameter suggestions.

While a recording is live the recorder answers `suggest_authoring` over the WS
and pushes the result back on its event channel. Once the browser is gone there
is no session to ask, but the LLM only needs the recorded steps — which are in
the DB — so the same suggestion runs as a queued `jobs:llm` job and leaves its
result in Redis for the workflow page to poll.

FastAPI enqueues and reads; the worker computes and writes (project rule: LLM
work happens only in the worker, and the two talk only through Redis).
"""

import uuid

# Long enough that a user can wander off mid-suggestion and still come back to
# it, short enough that a stale suggestion never outlives the page that asked.
RESULT_TTL_SECONDS = 15 * 60

JOB_KIND = "suggest_parameters"


def suggestions_key(workflow_id: uuid.UUID | str) -> str:
    return f"authoring:params:{workflow_id}"

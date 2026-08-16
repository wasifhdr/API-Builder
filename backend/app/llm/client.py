import asyncio
import contextvars
import json
import logging
import re
from dataclasses import dataclass

from openai import AsyncOpenAI, RateLimitError

from app.config import settings

log = logging.getLogger("llm")

# A 429 here is a per-MINUTE quota window, so waiting a full minute is what
# actually clears it — the retryDelay the API reports is often a fraction of a
# second and retrying on that alone just burns another attempt against the same
# exhausted window. One authoring run makes a call per drive turn plus one per
# field compiled, so bursts cross the ceiling routinely. Measured: a single 429
# killed an entire waltonbd run — the drive loop's exception escaped, the
# session ended with no marks, and it surfaced as "the agent never marked any
# data to extract", a marking failure that was never about marking.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_SLEEP_S = 60.0

# Set by app.agent.runner for the duration of a run, so a minute spent waiting
# out a quota window can be shown in the UI instead of looking like a hang.
# A ContextVar rather than a parameter: this client has no idea which run it is
# serving, threading a run id through every call site would push transport
# concerns into all of them, and a context is copied into the tasks the
# recording session spawns — exactly the scope that needs it.
rate_limit_listener: contextvars.ContextVar = contextvars.ContextVar(
    "rate_limit_listener", default=None,
)


async def _notify_rate_limit(waiting: bool) -> None:
    listener = rate_limit_listener.get()
    if listener is None:
        return
    try:
        await listener(waiting)
    except Exception:  # noqa: BLE001 - telling the UI must never fail the call
        log.exception("rate limit listener failed")


async def _create(**kwargs):
    """chat.completions.create, waiting out a rate-limit window on 429.

    Only rate limits are retried: every other error is a real failure the
    caller must see rather than pay for twice more. Note the cost — up to
    RATE_LIMIT_RETRIES - 1 minutes per call — which is charged against
    app.agent.runner's WALL_CLOCK_SECONDS.
    """
    notified = False
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            resp = await client.chat.completions.create(**kwargs)
        except RateLimitError:
            if attempt == RATE_LIMIT_RETRIES - 1:
                if notified:
                    await _notify_rate_limit(False)
                raise
            log.warning(
                "rate limited, waiting %.0fs for the quota window (attempt %s/%s)",
                RATE_LIMIT_SLEEP_S, attempt + 1, RATE_LIMIT_RETRIES,
            )
            if not notified:
                notified = True
                await _notify_rate_limit(True)
            await asyncio.sleep(RATE_LIMIT_SLEEP_S)
        else:
            if notified:
                await _notify_rate_limit(False)
            return resp

# Strips a reasoning block a thinking-capable model may emit even when not asked
# to. Covers <think>, <thought>, <thinking> — Google-served Gemma wraps its
# answer in <thought>…</thought> and often echoes the schema/example JSON INSIDE
# it, so this must run before we hunt for the first "{".
_THINK_BLOCK_RE = re.compile(r"<(think|thought|thinking)>.*?</\1>", re.DOTALL | re.IGNORECASE)
# Strips a ```json ... ``` (or bare ``` ... ```) fence some instruct models
# wrap structured output in.
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _build_client() -> AsyncOpenAI:
    if settings.llm_provider == "craftx":
        return AsyncOpenAI(
            base_url=settings.craftx_base_url,
            api_key=settings.craftx_api_key,
            timeout=180.0,
            max_retries=1,
        )
    # gemini (default): Google AI Studio's OpenAI-compatible endpoint.
    return AsyncOpenAI(
        base_url=settings.gemini_base_url,
        api_key=settings.gemini_api_key,
        timeout=180.0,
        max_retries=1,
    )


client = _build_client()


def _model_name() -> str:
    # Both providers are hosted and need the real model name.
    if settings.llm_provider == "craftx":
        return settings.craftx_model
    return settings.gemini_model


MODEL_NAME = _model_name()


def _extract_json(content: str) -> dict:
    """Defensively pulls a JSON object out of a chat completion's content.

    Both providers (craftx, gemini) are asked for JSON via a prompt-embedded
    schema, and the payload can still arrive wrapped in a <think>...</think>
    block, a markdown code fence, or with a sentence of prose before/after.
    Never assume the whole content is clean JSON; always route it through here
    first.
    """
    text = _THINK_BLOCK_RE.sub("", content).strip()

    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object found in LLM response: {content[:500]!r}")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])

    raise ValueError(f"unbalanced JSON object in LLM response: {content[:500]!r}")


async def complete_json(
    system: str,
    user: str,
    schema: dict,
    max_tokens: int = 2000,
    images: list[str] | None = None,
) -> dict:
    user = (
        f"{user}\n\nRespond with ONLY a JSON object matching this schema "
        f"(no prose, no markdown fence):\n{json.dumps(schema)}"
    )

    if images:
        user_content: object = [
            {"type": "text", "text": user},
            *(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
                for img in images
            ),
        ]
    else:
        user_content = user

    resp = await _create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
    )
    if not resp.choices:
        # A WAF/gateway in front of the model can return a 200 with an error
        # body (no choices) instead of a completion — e.g. Imunify360 bot
        # protection. Surface its message instead of an opaque IndexError.
        extra = resp.model_dump()
        detail = extra.get("message") or extra.get("error") or "gateway returned no choices"
        raise RuntimeError(f"LLM gateway error: {detail}")
    return _extract_json(resp.choices[0].message.content)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    # Provider-specific data that must be echoed back verbatim on the tool
    # call when it's replayed into a later turn's message history — e.g.
    # Gemini's thought_signature (extra_content.google.thought_signature).
    # Omitting it doesn't fail immediately; the API accepts a growing
    # number of turns without it and then rejects the whole conversation
    # once enough have accumulated, so this only shows up in genuinely
    # long agent runs, never in a short scripted test.
    raw_extra: dict | None = None


@dataclass(frozen=True)
class TurnResult:
    tool_calls: list[ToolCall]
    text: str | None
    usage_tokens: int


def user_message(text: str, screenshot_b64: str | None = None) -> dict:
    """One user turn, optionally carrying a screenshot as an inline image part.
    Mirrors the image handling complete_json already uses."""
    if screenshot_b64 is None:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
        ],
    }


def tool_result_message(tool_call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


# The agent loop drives an unfamiliar website across ~25 turns — a much harder
# task than the bounded structured extraction the default model was chosen for.
# Kept separate so the agent can be raised to a stronger model without making
# every spec-enrichment and field-naming call more expensive.
AGENT_MODEL_NAME = settings.agent_model or MODEL_NAME


async def complete_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4000,
    model: str | None = None,
) -> TurnResult:
    """One turn of a tool-calling conversation.

    The caller owns the message list and appends to it across turns. Tool call
    arguments arrive as a JSON *string* and, like every other structured output
    from these providers, can be fence- or think-wrapped — so they go through
    _extract_json rather than a bare json.loads.
    """
    kwargs: dict = {
        "model": model or AGENT_MODEL_NAME,
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = await _create(**kwargs)

    if not resp.choices:
        extra = resp.model_dump()
        detail = extra.get("message") or extra.get("error") or "gateway returned no choices"
        raise RuntimeError(f"LLM gateway error: {detail}")

    message = resp.choices[0].message
    calls: list[ToolCall] = []
    for raw in (getattr(message, "tool_calls", None) or []):
        try:
            arguments = _extract_json(raw.function.arguments)
        except ValueError:
            # A malformed argument payload is one bad turn, not a dead run —
            # surface it as an empty-argument call so the loop can tell the
            # model it failed rather than crashing the session.
            arguments = {}
        raw_extra = getattr(raw, "extra_content", None)
        calls.append(ToolCall(
            id=raw.id, name=raw.function.name, arguments=arguments, raw_extra=raw_extra,
        ))

    usage = getattr(resp, "usage", None)
    return TurnResult(
        tool_calls=calls,
        text=message.content,
        usage_tokens=getattr(usage, "total_tokens", 0) or 0,
    )

import json
import re
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.config import settings

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

    resp = await client.chat.completions.create(
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

    resp = await client.chat.completions.create(**kwargs)

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
        calls.append(ToolCall(id=raw.id, name=raw.function.name, arguments=arguments))

    usage = getattr(resp, "usage", None)
    return TurnResult(
        tool_calls=calls,
        text=message.content,
        usage_tokens=getattr(usage, "total_tokens", 0) or 0,
    )

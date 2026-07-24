import json
import re

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

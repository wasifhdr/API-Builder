import json
import re

from openai import AsyncOpenAI

from app.config import settings

# Strips a reasoning block a thinking-capable model might emit even when not
# asked to (e.g. if LLM_PROVIDER is later switched back to a thinking model).
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Strips a ```json ... ``` (or bare ``` ... ```) fence some instruct models
# wrap structured output in even when response_format is honored.
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _build_client() -> AsyncOpenAI:
    if settings.llm_provider == "craftx":
        return AsyncOpenAI(
            base_url=settings.craftx_base_url,
            api_key=settings.craftx_api_key,
            timeout=180.0,
            max_retries=1,
        )
    return AsyncOpenAI(
        base_url=settings.llama_base_url,
        api_key="sk-local",  # ignored by llama-server unless --api-key is set
        timeout=180.0,
        max_retries=1,
    )


client = _build_client()

# llama-server serves a single model, so its name is cosmetic; CraftX is a
# multi-model gateway and requires the real model name.
MODEL_NAME = settings.craftx_model if settings.llm_provider == "craftx" else "local"


def _extract_json(content: str) -> dict:
    """Defensively pulls a JSON object out of a chat completion's content.

    response_format={"type": "json_schema", ...} is honored by llama.cpp, but a
    hosted gateway in front of a different model may ignore it — the payload can
    arrive wrapped in a <think>...</think> block, a markdown code fence, or with
    a sentence of prose before/after. Never assume the whole content is clean
    JSON; always route it through here first.
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


async def complete_json(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict:
    resp = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "out", "schema": schema, "strict": True},
        },
    )
    return _extract_json(resp.choices[0].message.content)

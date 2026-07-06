import json

from openai import AsyncOpenAI

from app.config import settings

client = AsyncOpenAI(
    base_url=settings.llama_base_url,
    api_key="sk-local",  # ignored by llama-server unless --api-key is set
    timeout=180.0,
    max_retries=1,
)


async def complete_json(system: str, user: str, schema: dict, max_tokens: int = 1200) -> dict:
    resp = await client.chat.completions.create(
        model="local",  # llama-server serves a single model; the name is cosmetic
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
    return json.loads(resp.choices[0].message.content)

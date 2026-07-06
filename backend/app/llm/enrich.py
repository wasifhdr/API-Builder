import logging
from urllib.parse import urlparse

from openapi_spec_validator import validate

from app.llm.client import complete_json
from app.llm.prompts import build_enrichment_schema, build_prompt

log = logging.getLogger("llm")


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def _merge(spec: dict, enrichment: dict) -> None:
    spec["info"]["description"] = enrichment["api_description"]
    path = next(iter(spec["paths"].values()))
    path["get"]["summary"] = enrichment["endpoint_summary"]
    if enrichment.get("tags"):
        path["get"]["tags"] = enrichment["tags"]
    for p in path["get"]["parameters"]:
        key = f"param_{p['name']}"
        if key in enrichment:
            p["description"] = enrichment[key]


async def enrich_spec(
    spec: dict,
    name: str,
    start_url: str,
    steps: list[dict],
    parameters: list[dict],
    sample_output: object,
) -> dict:
    """Mutates spec in place with LLM-written prose. Raises on failure — the
    caller (handlers.generate_spec) falls back to the unenriched skeleton."""
    domain = _domain(start_url)
    schema = build_enrichment_schema(parameters)
    system, user = build_prompt(name, domain, steps, parameters, sample_output)

    last_error: Exception | None = None
    for attempt in range(2):  # one retry, with the validator error appended to the prompt
        prompt = user if attempt == 0 else f"{user}\n\nThe previous attempt was invalid: {last_error}. Fix it."
        try:
            enrichment = await complete_json(system, prompt, schema)
            _merge(spec, enrichment)
            validate(spec)
            return spec
        except Exception as exc:
            last_error = exc
            log.warning("spec enrichment attempt %s failed: %s", attempt, exc)

    raise RuntimeError(f"enrichment failed after retry: {last_error}")

import re

from app.llm.client import complete_json
from app.recorder.session import VALID_PARAM_TYPES

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "summary": {"type": "string"},
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(VALID_PARAM_TYPES)},
                    "required": {"type": "boolean"},
                    "drive_value": {"type": "string"},
                    "verify_value": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "type", "drive_value", "verify_value"],
            },
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "type": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    "required": ["url", "parameters", "fields"],
}

PLAN_SYSTEM = (
    "You plan web-scraping workflows. Given a user's request in plain English, "
    "you decide which site to start on, which inputs become API parameters, and "
    "which data fields the API returns. You do not browse — you only plan.\n\n"
    "Rules:\n"
    "- Pick the most likely official site for the described target.\n"
    "- For every parameter, give TWO different realistic example values: "
    "drive_value (used while building) and verify_value (used to prove the "
    "parameter actually works). They MUST be different and MUST produce "
    "different results.\n"
    "- Parameter names are lowercase snake_case.\n"
    "- Field names describe the data, not the markup: title, price, url."
)


class PlanError(Exception):
    """The model's plan was unusable."""


async def build_plan(prompt: str) -> dict:
    raw = await complete_json(
        PLAN_SYSTEM,
        f"User request: {prompt}",
        PLAN_SCHEMA,
        max_tokens=1500,
    )

    url = str(raw.get("url") or "")
    if not url.startswith(("http://", "https://")):
        raise PlanError(f"plan did not produce an http(s) start URL: {url!r}")

    fields = [f for f in (raw.get("fields") or []) if f.get("name")]
    if not fields:
        raise PlanError("plan declared no output fields")

    parameters = []
    for item in raw.get("parameters") or []:
        name = str(item.get("name") or "")
        if not _NAME_RE.match(name):
            raise PlanError(f"unsafe parameter name {name!r}")
        drive = str(item.get("drive_value") or "")
        verify = str(item.get("verify_value") or "")
        if not drive or not verify:
            raise PlanError(f"parameter {name!r} is missing an example value")
        if drive == verify:
            # Without two distinct values the differential verify check cannot
            # tell a working parameter from a hardcoded one.
            raise PlanError(f"parameter {name!r} needs distinct drive and verify values")
        ptype = item.get("type")
        parameters.append({
            "name": name,
            "type": ptype if ptype in VALID_PARAM_TYPES else "string",
            "required": bool(item.get("required", True)),
            "drive_value": drive,
            "verify_value": verify,
            "description": item.get("description") or None,
        })

    return {
        "url": url,
        "summary": raw.get("summary") or "",
        "parameters": parameters,
        "fields": fields,
    }

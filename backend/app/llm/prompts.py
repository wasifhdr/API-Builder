import json


def _humanize_step(step: dict) -> str:
    stype = step.get("type")
    if stype == "goto":
        return f"opens {step.get('url')}"
    if stype == "click":
        return "clicks an element"
    if stype == "fill":
        value = step.get("value") or {}
        if "param" in value:
            return f"types {{{value['param']}}} into a field"
        return "types text into a field"
    if stype == "press":
        return f"presses {step.get('key')}"
    if stype == "select_option":
        return "selects an option"
    if stype == "extract":
        return "extracts data from the page"
    if stype == "scroll_page":
        return "scrolls for more results"
    return stype or "performs an action"


def humanize_steps(steps: list[dict]) -> str:
    return "; ".join(_humanize_step(s) for s in steps)


def build_enrichment_schema(parameters: list[dict]) -> dict:
    properties = {
        "api_description": {"type": "string", "maxLength": 400},
        "endpoint_summary": {"type": "string", "maxLength": 120},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    }
    required = ["api_description", "endpoint_summary", "tags"]
    for p in parameters:
        key = f"param_{p['name']}"
        properties[key] = {"type": "string", "maxLength": 200}
        required.append(key)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def build_prompt(name: str, domain: str, steps: list[dict], parameters: list[dict], sample_output: object) -> tuple[str, str]:
    system = (
        "You are documenting an auto-generated JSON API for a non-technical user. "
        "Write concise, factual descriptions. Do not invent parameters or fields "
        "that are not listed below. At most 2 sentences per description."
    )

    sample_str = json.dumps(sample_output, default=str)[:1500] if sample_output is not None else "(no sample captured)"
    param_lines = "\n".join(f"- {p['name']} (example: {p.get('example')})" for p in parameters) or "(none)"

    user = (
        f"API name: {name}\n"
        f"Target site: {domain}\n"
        f"Steps: {humanize_steps(steps)}\n"
        f"Parameters:\n{param_lines}\n"
        f"Sample output (truncated): {sample_str}\n\n"
        "Write: api_description (what this API does, at most 2 sentences), "
        "endpoint_summary (a short title), one description per parameter "
        "(the param_<name> keys), and up to 3 tags."
    )
    return system, user

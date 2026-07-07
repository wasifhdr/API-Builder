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


def build_parameter_suggestion_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["parameters"],
        "properties": {
            "parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["step_i", "name", "type", "example", "description", "confidence"],
                    "properties": {
                        "step_i": {"type": "integer"},
                        "name": {"type": "string", "maxLength": 60},
                        "type": {"type": "string", "enum": ["string", "integer", "number", "boolean"]},
                        "example": {"type": "string", "maxLength": 200},
                        "description": {"type": "string", "maxLength": 200},
                        "confidence": {"type": "number"},
                    },
                },
            },
        },
    }


def build_parameter_suggestion_prompt(steps: list[dict], candidates: list[dict]) -> tuple[str, str]:
    system = (
        "You help authors of a browser-automation API decide which recorded form "
        "inputs should become caller-supplied parameters. Only suggest parameters "
        "for the candidate steps listed below — never invent a step_i that is not "
        "in the candidate list, and return at most one suggestion per step_i. Use "
        "short snake_case or camelCase names that describe what the field means "
        "to a caller (e.g. 'query', 'page', 'zip_code'), never generic names like "
        "'input1'. Infer 'type' from the example value: whole numbers -> integer, "
        "decimals -> number, true/false-like text -> boolean, anything else -> "
        "string. Skip candidates that look like fixed, site-specific values "
        "rather than something a caller would want to vary."
    )

    candidate_lines = "\n".join(
        f"- step_i={c['i']}: selector={c['selector'] or '?'} value={c['literal']!r}"
        for c in candidates
    ) or "(none)"

    user = (
        f"Recorded flow: {humanize_steps(steps)}\n\n"
        f"Candidate steps (only these may become parameters):\n{candidate_lines}\n\n"
        "Return one entry in 'parameters' for each candidate worth exposing as a "
        "parameter."
    )
    return system, user


def build_extraction_naming_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["fields"],
        "properties": {
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["selector", "name", "take", "transform"],
                    "properties": {
                        "selector": {"type": "string", "maxLength": 300},
                        "name": {"type": "string", "maxLength": 60},
                        "take": {"type": "string", "enum": ["text", "html", "attr:href", "attr:src"]},
                        "transform": {"type": "string", "enum": ["none", "trim", "number", "abs_url"]},
                    },
                },
            },
        },
    }


def _truncate_sample_value(value: object, limit: int = 200) -> object:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _first_sample_row(config: dict, sample: object) -> dict:
    if config.get("mode") == "list":
        if isinstance(sample, list) and sample and isinstance(sample[0], dict):
            return sample[0]
        return {}
    return sample if isinstance(sample, dict) else {}


def build_extraction_naming_prompt(config: dict, sample: object) -> tuple[str, str]:
    system = (
        "You help authors of a browser-automation API name the fields it "
        "extracts from a page. Given each field's CSS selector, its current "
        "'take' mode, and a sample value, propose a clear snake_case field name "
        "and the most appropriate 'take'/'transform' pairing: 'text' for visible "
        "text, 'attr:href' for link fields, 'attr:src' for image fields; "
        "'transform' should be 'number' for numeric-looking text, 'abs_url' for "
        "relative links/images, 'trim' to strip whitespace, or 'none' otherwise. "
        "Do not invent fields or selectors that are not listed below, and return "
        "exactly one entry per field listed, using its exact selector."
    )

    fields = config.get("fields") or []
    row = _first_sample_row(config, sample)
    field_lines = "\n".join(
        f"- selector={f.get('selector')} take={f.get('take')} "
        f"sample={_truncate_sample_value(row.get(f.get('name')))!r}"
        for f in fields
    ) or "(none)"

    user = (
        f"Extraction mode: {config.get('mode')}\n"
        f"Fields:\n{field_lines}\n\n"
        "Return one entry per field, in the same order, using its exact selector."
    )
    return system, user

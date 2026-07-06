PARAM_TYPE_TO_SCHEMA = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
}

ERROR_SCHEMA = {"type": "object", "properties": {"detail": {}}}


def _query_parameters(parameters: list[dict]) -> list[dict]:
    query_params = []
    for p in parameters:
        schema = dict(PARAM_TYPE_TO_SCHEMA.get(p.get("type", "string"), {"type": "string"}))
        if p.get("example") is not None:
            schema["example"] = p["example"]
        query_params.append({
            "name": p["name"],
            "in": "query",
            "required": bool(p.get("required", True)),
            "schema": schema,
            "description": p.get("description") or f"The '{p['name']}' parameter.",
        })
    return query_params


def _responses(output_schema: dict | None) -> dict:
    data_schema = output_schema or {"type": "object", "additionalProperties": True}

    success_schema = {
        "type": "object",
        "required": ["data", "meta"],
        "properties": {
            "data": data_schema,
            "meta": {
                "type": "object",
                "properties": {
                    "cached": {"type": "boolean"},
                    "duration_ms": {"type": "integer"},
                    "execution_id": {"type": "string"},
                },
            },
        },
    }

    def _err(description: str) -> dict:
        return {"description": description, "content": {"application/json": {"schema": ERROR_SCHEMA}}}

    return {
        "200": {"description": "Successful response", "content": {"application/json": {"schema": success_schema}}},
        "202": {
            "description": "Execution queued — poll status_url for the result",
            "content": {"application/json": {"schema": {
                "type": "object",
                "properties": {"execution_id": {"type": "string"}, "status_url": {"type": "string"}},
            }}},
        },
        "401": _err("Missing or invalid API key"),
        "403": _err("No access to this API, or it has been disabled"),
        "404": _err("API not found"),
        "422": _err("Parameter validation failed"),
        "429": _err("Rate limit exceeded"),
        "502": _err("The recorded workflow failed to replay"),
    }


def build_skeleton(
    name: str,
    slug: str,
    description: str | None,
    parameters: list[dict],
    output_schema: dict | None,
) -> dict:
    """Deterministic OpenAPI 3.1 skeleton — always valid, independent of the LLM."""
    operation_id = f"run_{slug.replace('-', '_')}"

    return {
        "openapi": "3.1.0",
        "info": {
            "title": name,
            "description": description or f"Runs the '{name}' workflow and returns the extracted data as JSON.",
            "version": "1.0.0",
        },
        "servers": [{"url": "http://localhost:8000"}],
        "paths": {
            f"/v1/run/{slug}": {
                "get": {
                    "summary": f"Run {name}",
                    "operationId": operation_id,
                    "parameters": _query_parameters(parameters),
                    "security": [{"ApiKeyAuth": []}],
                    "responses": _responses(output_schema),
                }
            }
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            }
        },
    }

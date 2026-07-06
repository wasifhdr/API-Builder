from openapi_spec_validator import validate

from app.llm.spec_builder import build_skeleton


def test_list_mode_output_schema_validates():
    output_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "price": {"type": "integer"}},
        },
    }
    parameters = [
        {"name": "query", "type": "string", "required": True, "example": "physics"},
    ]
    spec = build_skeleton("Book Search", "book-search-ab12", None, parameters, output_schema)
    validate(spec)

    op = spec["paths"]["/v1/run/book-search-ab12"]["get"]
    assert op["parameters"][0]["name"] == "query"
    assert op["parameters"][0]["required"] is True
    assert op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["data"] == output_schema


def test_single_mode_output_schema_validates():
    output_schema = {"type": "object", "properties": {"title": {"type": "string"}}}
    spec = build_skeleton("Page Title", "page-title-cd34", None, [], output_schema)
    validate(spec)
    assert spec["paths"]["/v1/run/page-title-cd34"]["get"]["parameters"] == []


def test_no_output_schema_falls_back_to_open_object():
    spec = build_skeleton("Nothing Extracted Yet", "slug-ef56", None, [], None)
    validate(spec)
    data_schema = spec["paths"]["/v1/run/slug-ef56"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["data"]
    assert data_schema == {"type": "object", "additionalProperties": True}


def test_all_parameter_types_map_correctly():
    parameters = [
        {"name": "q", "type": "string", "required": True},
        {"name": "page", "type": "integer", "required": False},
        {"name": "min_price", "type": "number", "required": False},
        {"name": "in_stock", "type": "boolean", "required": False},
    ]
    spec = build_skeleton("Typed Params", "typed-gh78", None, parameters, None)
    validate(spec)
    by_name = {p["name"]: p for p in spec["paths"]["/v1/run/typed-gh78"]["get"]["parameters"]}
    assert by_name["q"]["schema"]["type"] == "string"
    assert by_name["page"]["schema"]["type"] == "integer"
    assert by_name["min_price"]["schema"]["type"] == "number"
    assert by_name["in_stock"]["schema"]["type"] == "boolean"


def test_security_scheme_present():
    spec = build_skeleton("Any", "any-ij90", None, [], None)
    validate(spec)
    assert spec["components"]["securitySchemes"]["ApiKeyAuth"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key",
    }
    assert spec["paths"]["/v1/run/any-ij90"]["get"]["security"] == [{"ApiKeyAuth": []}]

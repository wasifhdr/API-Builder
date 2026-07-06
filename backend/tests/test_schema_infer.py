from app.recorder.schema_infer import infer_schema


def test_infers_array_of_objects():
    sample = [
        {"title": "a", "price": 350},
        {"title": "b", "price": 420},
    ]
    schema = infer_schema(sample)
    assert schema["type"] == "array"
    item_props = schema["items"]["properties"]
    assert item_props["title"]["type"] == "string"
    assert item_props["price"]["type"] == "integer"


def test_infers_single_object():
    schema = infer_schema({"title": "Fixture Shop"})
    assert schema["type"] == "object"
    assert schema["properties"]["title"]["type"] == "string"


def test_merges_schema_across_list_items_with_nulls():
    sample = [{"title": "a", "price": 350}, {"title": "b", "price": None}]
    schema = infer_schema(sample)
    price_type = schema["items"]["properties"]["price"]["type"]
    assert "integer" in price_type or "null" in price_type

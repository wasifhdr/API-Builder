import pytest

from app.services.param_coercion import ParamCoercionError, coerce_params

PARAMS = [
    {"name": "query", "type": "string", "required": True},
    {"name": "page", "type": "integer", "required": False},
    {"name": "min_price", "type": "number", "required": False},
    {"name": "in_stock", "type": "boolean", "required": False},
]


def test_coerces_all_types_correctly():
    result = coerce_params(PARAMS, {"query": "books", "page": "2", "min_price": "9.99", "in_stock": "true"})
    assert result == {"query": "books", "page": 2, "min_price": 9.99, "in_stock": True}


def test_optional_params_omitted_when_absent():
    result = coerce_params(PARAMS, {"query": "books"})
    assert result == {"query": "books"}


def test_missing_required_param_raises():
    with pytest.raises(ParamCoercionError) as exc_info:
        coerce_params(PARAMS, {})
    assert any("query" in e for e in exc_info.value.errors)


def test_bad_integer_raises():
    with pytest.raises(ParamCoercionError) as exc_info:
        coerce_params(PARAMS, {"query": "books", "page": "not-a-number"})
    assert any("page" in e for e in exc_info.value.errors)


def test_boolean_coercion_variants():
    assert coerce_params(PARAMS, {"query": "x", "in_stock": "1"})["in_stock"] is True
    assert coerce_params(PARAMS, {"query": "x", "in_stock": "false"})["in_stock"] is False

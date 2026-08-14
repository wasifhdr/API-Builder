from unittest.mock import AsyncMock, patch

import pytest

from app.agent.planner import PlanError, build_plan


def _plan(**overrides):
    plan = {
        "url": "https://waltonbd.com",
        "summary": "Search Walton products",
        "parameters": [{
            "name": "query", "type": "string", "required": True,
            "drive_value": "refrigerator", "verify_value": "television",
            "description": "Search term",
        }],
        "fields": [{"name": "title", "type": "string"},
                   {"name": "price", "type": "string"}],
    }
    plan.update(overrides)
    return plan


@pytest.mark.asyncio
async def test_build_plan_returns_a_validated_plan():
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan())):
        plan = await build_plan("make me an API to search walton")

    assert plan["url"] == "https://waltonbd.com"
    assert plan["parameters"][0]["drive_value"] == "refrigerator"
    assert plan["parameters"][0]["verify_value"] == "television"


@pytest.mark.asyncio
async def test_build_plan_rejects_identical_drive_and_verify_values():
    bad = _plan(parameters=[{
        "name": "query", "type": "string", "required": True,
        "drive_value": "same", "verify_value": "same", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=bad)):
        with pytest.raises(PlanError, match="distinct"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_rejects_a_non_http_url():
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan(url="ftp://x"))):
        with pytest.raises(PlanError, match="http"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_rejects_unsafe_parameter_names():
    bad = _plan(parameters=[{
        "name": "drop table", "type": "string", "required": True,
        "drive_value": "a", "verify_value": "b", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=bad)):
        with pytest.raises(PlanError, match="name"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_rejects_an_empty_field_list():
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan(fields=[]))):
        with pytest.raises(PlanError, match="field"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_coerces_an_unknown_type_to_string():
    odd = _plan(parameters=[{
        "name": "query", "type": "wat", "required": True,
        "drive_value": "a", "verify_value": "b", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=odd)):
        plan = await build_plan("p")
    assert plan["parameters"][0]["type"] == "string"


@pytest.mark.asyncio
async def test_build_plan_rejects_a_parameter_missing_an_example_value():
    bad = _plan(parameters=[{
        "name": "query", "type": "string", "required": True,
        "drive_value": "", "verify_value": "b", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=bad)):
        with pytest.raises(PlanError, match="example"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_keeps_a_declared_result_shape():
    with patch("app.agent.planner.complete_json",
               AsyncMock(return_value=_plan(result_shape="detail"))):
        plan = await build_plan("get the specs of the Walton WNR-6D6")
    assert plan["result_shape"] == "detail"


@pytest.mark.asyncio
async def test_build_plan_falls_back_to_list_when_the_shape_is_missing():
    """An unparseable cardinality is not worth failing a run over — the
    value-presence and selector-stability checks catch a wrong fallback."""
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan())):
        plan = await build_plan("make me an API to search walton")
    assert plan["result_shape"] == "list"


@pytest.mark.asyncio
async def test_build_plan_falls_back_to_list_on_a_garbage_shape():
    with patch("app.agent.planner.complete_json",
               AsyncMock(return_value=_plan(result_shape="table"))):
        plan = await build_plan("p")
    assert plan["result_shape"] == "list"

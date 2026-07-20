from app.recorder.extraction import run_extraction


async def test_list_extraction_text_and_number_transform(fixture_page):
    config = {
        "mode": "list",
        "root": ".book-item",
        "fields": [
            {"name": "title", "selector": ".book-title", "take": "text"},
            {"name": "price", "selector": ".book-price", "take": "text", "transform": "number"},
        ],
    }
    result = await run_extraction(fixture_page, config)
    assert len(result) == 3
    assert result[0] == {"title": "Physics 101", "price": 350}
    assert result[1]["title"] == "Chemistry Basics"
    assert result[2]["price"] == 500


async def test_abs_url_transform_resolves_relative_links(fixture_page):
    config = {
        "mode": "list",
        "root": ".book-item",
        "fields": [
            {"name": "url", "selector": ".book-link", "take": "attr:href", "transform": "abs_url"},
            {"name": "cover", "selector": ".book-cover", "take": "attr:src", "transform": "abs_url"},
        ],
    }
    result = await run_extraction(fixture_page, config)
    assert result[0]["url"].startswith("http://127.0.0.1")
    assert result[0]["url"].endswith("/books/1")
    assert result[0]["cover"].endswith("/covers/1.jpg")


async def test_single_mode_extracts_one_object(fixture_page):
    config = {
        "mode": "single",
        "fields": [{"name": "title", "selector": ".page-title", "take": "text"}],
    }
    result = await run_extraction(fixture_page, config)
    assert result == {"title": "Fixture Shop"}


async def test_missing_selector_yields_none_not_a_crash(fixture_page):
    config = {
        "mode": "single",
        "fields": [{"name": "missing", "selector": ".does-not-exist", "take": "text"}],
    }
    result = await run_extraction(fixture_page, config)
    assert result == {"missing": None}


async def test_html_take_and_trim_transform(fixture_page):
    config = {
        "mode": "list",
        "root": ".book-item",
        "fields": [{"name": "title_html", "selector": ".book-title", "take": "html", "transform": "trim"}],
    }
    result = await run_extraction(fixture_page, config)
    assert result[0]["title_html"] == "Physics 101"


async def test_extract_empty_selector_yields_null(fixture_page):
    # A field with no selector (normal in LLM-mode configs) must not crash the
    # selector path with a querySelector('') SyntaxError — it yields null.
    config = {"mode": "single", "fields": [{"name": "blank", "selector": "", "take": "text"}]}
    result = await run_extraction(fixture_page, config)
    assert result == {"blank": None}

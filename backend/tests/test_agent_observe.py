import pytest

from app.agent.observe import RefNotFound, observe, resolve_ref


@pytest.mark.asyncio
async def test_observe_lists_interactive_elements(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    obs = await observe(fixture_page, with_screenshot=False)

    assert "[ref_0]" in obs.tree
    assert "input" in obs.tree
    assert "Search products" in obs.tree
    assert obs.ref_count > 0
    assert obs.url.endswith("/search.html")


@pytest.mark.asyncio
async def test_observe_surfaces_repeated_content_blocks(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html?q=television")
    obs = await observe(fixture_page, with_screenshot=False)
    assert "Smart Television" in obs.tree


@pytest.mark.asyncio
async def test_observe_does_not_tag_the_dom(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    await observe(fixture_page, with_screenshot=False)
    html = await fixture_page.content()
    assert "data-ab-" not in html


@pytest.mark.asyncio
async def test_resolve_ref_returns_the_element(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    await observe(fixture_page, with_screenshot=False)
    handle = await resolve_ref(fixture_page, "ref_0")
    assert (await handle.evaluate("el => el.tagName")).lower() in {"input", "button", "a", "form"}


@pytest.mark.asyncio
async def test_resolve_ref_rejects_unknown_ref(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    await observe(fixture_page, with_screenshot=False)
    with pytest.raises(RefNotFound):
        await resolve_ref(fixture_page, "ref_9999")


@pytest.mark.asyncio
async def test_observe_captures_a_screenshot(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    obs = await observe(fixture_page, with_screenshot=True)
    assert len(obs.screenshot_b64) > 100

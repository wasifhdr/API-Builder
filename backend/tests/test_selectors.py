from bs4 import BeautifulSoup

from app.recorder.selectors import rank_selectors


def _parse(html: str, selector: str):
    soup = BeautifulSoup(html, "lxml")
    return soup.select_one(selector)


def test_data_testid_wins_over_everything():
    el = _parse('<div id="real-id" data-testid="search-box" name="q"></div>', "div")
    assert rank_selectors(el)[0] == '[data-testid="search-box"]'


def test_real_id_used_when_no_testid():
    el = _parse('<input id="search-input" name="q">', "input")
    assert rank_selectors(el)[0] == "#search-input"


def test_generated_looking_id_is_skipped():
    el = _parse('<input id="a1b2c3d4e5f6" name="q">', "input")
    candidates = rank_selectors(el)
    assert "#a1b2c3d4e5f6" not in candidates
    assert candidates[0] == '[name="q"]'


def test_uuid_like_id_is_skipped():
    el = _parse('<div id="3f9e8d7c-1234-4abc-9def-0123456789ab" name="row"></div>', "div")
    candidates = rank_selectors(el)
    assert not any(c.startswith("#") for c in candidates)


def test_name_used_when_no_id():
    el = _parse('<input name="query">', "input")
    assert rank_selectors(el)[0] == '[name="query"]'


def test_role_and_aria_label_fallback():
    el = _parse('<div role="button" aria-label="Submit search"></div>', "div")
    assert rank_selectors(el)[0] == '[role="button"][aria-label="Submit search"]'


def test_css_path_fallback_with_nth_of_type():
    html = """
    <ul>
      <li>one</li>
      <li>two</li>
      <li id="a1b2c3d4">three</li>
    </ul>
    """
    el = _parse(html, "li:nth-of-type(3)")
    candidates = rank_selectors(el)
    assert candidates[-1].endswith("li:nth-of-type(3)")


def test_top_three_only():
    el = _parse(
        '<button id="submit-btn" data-testid="submit" name="go" role="button" '
        'aria-label="Go"></button>',
        "button",
    )
    assert len(rank_selectors(el)) == 3

from bs4 import BeautifulSoup

from app.recorder.selectors import css_path, rank_selectors


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


def test_aria_label_without_role_is_tag_qualified():
    # Native <button> carries an implicit ARIA role but no literal role attr;
    # its aria-label is still a stable selector and must not be dropped.
    el = _parse('<button aria-label="Dismiss sign-in info." type="button"></button>', "button")
    candidates = rank_selectors(el)
    assert '[aria-label="Dismiss sign-in info."]' in candidates[0]
    assert candidates[0] == 'button[aria-label="Dismiss sign-in info."]'


def test_aria_label_without_role_beats_css_path():
    html = '<div><div><span aria-label="Close"></span></div></div>'
    el = _parse(html, "span")
    candidates = rank_selectors(el)
    aria = 'span[aria-label="Close"]'
    css_path = candidates[-1]
    # aria-label candidate must rank ahead of the positional css_path fallback.
    assert aria in candidates
    assert candidates.index(aria) < candidates.index(css_path)


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


# --- uniqueness: an ambiguous selector doesn't fail loudly at replay, it acts
# on the wrong node, so a candidate that resolves to one element outranks a
# nominally-stronger attribute that resolves to many.


def test_ambiguous_candidate_ranks_below_unique_one():
    html = """
    <div data-testid="row"><a class="open-detail-btn">a</a></div>
    <div data-testid="row"><a class="delete-row-btn">b</a></div>
    """
    el = _parse(html, ".delete-row-btn")
    candidates = rank_selectors(el)
    assert candidates[0] == "a.delete-row-btn"


def test_ambiguous_testid_demoted_below_unique_class():
    # A shared data-testid is the strongest attribute here and still loses: it
    # can't tell the two buttons apart, and the class can.
    html = """
    <button data-testid="action" class="cancel-btn">Cancel</button>
    <button data-testid="action" class="confirm-btn">Confirm</button>
    """
    el = _parse(html, ".confirm-btn")
    candidates = rank_selectors(el)
    assert candidates[0] == "button.confirm-btn"
    assert candidates[0] != '[data-testid="action"]'


def test_ambiguous_id_demoted_below_unique_path():
    # Duplicate ids are invalid HTML but common in the wild, and "#dup" selects
    # both, so it's kept only as a fallback behind something that doesn't.
    html = '<div><span id="dup">one</span><span id="dup">two</span></div>'
    el = _parse(html, "span:nth-of-type(2)")
    candidates = rank_selectors(el)
    assert candidates[0] == "div > span:nth-of-type(2)"
    assert candidates.index("div > span:nth-of-type(2)") < candidates.index("#dup")


# --- anchoring: a floating positional path matches anywhere in the document


def test_css_path_is_anchored_at_nearest_stable_ancestor():
    html = """
    <section data-testid="results"><div><div><a>Exact matches</a></div></div></section>
    """
    el = _parse(html, "a")
    path = css_path(el)
    assert path == '[data-testid="results"] > div > div > a'


def test_css_path_anchors_beyond_the_contiguous_window_as_descendant():
    # The anchor sits further up than the spelled-out levels, so the path is
    # joined with a descendant combinator rather than listing every level.
    html = """
    <section id="find-results">
      <div><div><div><div><div><a>go</a></div></div></div></div></div>
    </section>
    """
    el = _parse(html, "a")
    path = css_path(el)
    assert path.startswith("#find-results ")
    assert " > " in path.split(" ", 1)[1]


def test_css_path_without_any_anchor_stays_relative():
    html = "<div><div><div><a>go</a></div></div></div>"
    el = _parse(html, "a")
    assert css_path(el) == "div > div > div > a"


# --- new identity-carrying candidates


def test_hashed_css_in_js_classes_are_rejected():
    # styled-components / emotion class names carry a build hash and change on
    # every deploy, so a selector pinned to one breaks silently later.
    html = '<div><button class="sc-b5df6b60-0 bycWPN ldgsgc">Go</button></div>'
    el = _parse(html, "button")
    assert not any(".sc-b5df6b60-0" in c or "bycWPN" in c or "ldgsgc" in c for c in rank_selectors(el))


def test_single_word_class_is_not_treated_as_stable():
    # No separator is indistinguishable from a short hash, so it's not used.
    html = '<div><button class="btn">Go</button></div>'
    el = _parse(html, "button")
    assert "button.btn" not in rank_selectors(el)


def test_text_selector_offered_for_interactive_element():
    html = '<nav><div><a href="/x">Exact matches</a></div></nav>'
    el = _parse(html, "a")
    assert 'a:has-text("Exact matches")' in rank_selectors(el)


def test_text_selector_not_offered_for_plain_container():
    # :has-text on a <div> would match every wrapper up the tree.
    html = "<section><div><div>Some body copy</div></div></section>"
    el = _parse(html, "div div")
    assert not any(":has-text" in c for c in rank_selectors(el))


def test_long_text_is_not_used_as_a_selector():
    text = "a" * 60
    html = f'<nav><div><a href="/x">{text}</a></div></nav>'
    el = _parse(html, "a")
    assert not any(":has-text" in c for c in rank_selectors(el))


def test_href_is_a_fallback_not_the_lead_candidate():
    # A recorded href embeds the values typed while recording, so it goes stale
    # as soon as the workflow runs with different parameters.
    html = '<section data-testid="results"><div><a href="/find?q=obsession">Exact matches</a></div></section>'
    el = _parse(html, "a")
    candidates = rank_selectors(el)
    href = 'a[href="/find?q=obsession"]'
    assert href in candidates
    assert candidates[0] != href


def test_attribute_values_with_quotes_are_escaped():
    el = _parse("""<button aria-label='Say "hi"'></button>""", "button")
    assert r'button[aria-label="Say \"hi\""]' in rank_selectors(el)


def test_imdb_exact_matches_regression():
    # The shape that produced "none of the candidate selectors matched": an <a>
    # with no testid/id/name/aria, whose only recorded selector was a floating
    # 4-level positional path that matched 28 nodes on the real page.
    html = """
    <nav><aside><div><div><div><a href="/">home</a></div></div></div></aside></nav>
    <div class="page-grid-item">
      <section data-testid="find-results-section-title">
        <div class="ipc-title">
          <div class="ipc-title__wrapper">
            <hgroup><h3>Titles</h3></hgroup>
            <div class="ipc-title__actions">
              <a class="ipc-btn ipc-btn--core-base results-section-exact-match-btn"
                 href="/find/?q=obsession&amp;exact=true">Exact matches</a>
            </div>
          </div>
        </div>
        <div class="other"></div>
      </section>
    </div>
    """
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one("a.results-section-exact-match-btn")
    candidates = rank_selectors(el)

    # The lead candidate must resolve to exactly this element, and must not be
    # the bare positional path that also matches the hidden nav-drawer link.
    lead = candidates[0]
    assert lead != "div:nth-of-type(1) > div > div > a"
    matched = soup.select(lead)
    assert len(matched) == 1 and matched[0] is el

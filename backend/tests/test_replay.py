import uuid
from urllib.parse import quote

import pytest

from app.config import settings
from app.recorder import llm_extract, selector_compiler
from app.recorder.replay import ReplayError, replay_workflow


async def test_happy_path_goto_and_extract(fixture_site_url):
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "root": ".book-item",
                "fields": [
                    {"name": "title", "selector": ".book-title", "take": "text"},
                    {"name": "price", "selector": ".book-price", "take": "text", "transform": "number"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert len(result["data"]) == 3
    assert result["data"][0] == {"title": "Physics 101", "price": 350}


async def test_param_substitution_in_fill(fixture_site_url):
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "fill", "selectors": ["#search-input"], "value": {"param": "query"}},
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {"mode": "single", "fields": [{"name": "echoed", "selector": "#search-echo", "take": "text"}]}
        },
    }
    result = await replay_workflow(snapshot, {"query": "physics book"}, None, uuid.uuid4())
    assert result["data"] == {"echoed": "physics book"}


async def test_selector_fallback_uses_second_candidate(fixture_site_url):
    # First candidate doesn't exist on the page; replay must fall through to
    # the second one rather than failing outright.
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {
                "i": 1,
                "type": "fill",
                "selectors": ["#this-id-does-not-exist", "#search-input"],
                "value": {"literal": "fallback worked"},
            },
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {"mode": "single", "fields": [{"name": "echoed", "selector": "#search-echo", "take": "text"}]}
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"] == {"echoed": "fallback worked"}


async def test_ambiguous_selector_binds_to_first_visible_match():
    # A recorded positional path often matches many nodes; the first in document
    # order is routinely a hidden copy in a collapsed nav drawer. Binding to it
    # (Playwright's .first) times out waiting for visibility while the element
    # the user actually clicked sits visible further down the page.
    html = """
    <div id='drawer' style='display:none'><div><div><a id='hidden-link' href='#'>go</a></div></div></div>
    <div><div><div><a id='real-link' href='#'>go</a></div></div></div>
    <div id='out'></div>
    <script>
    document.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        document.getElementById('out').textContent = a.id;
      });
    });
    </script>
    """
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "click", "selectors": ["div > div > div > a"]},
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {"mode": "single", "fields": [{"name": "clicked", "selector": "#out", "take": "text"}]}
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"] == {"clicked": "real-link"}


async def test_replay_uses_recorded_viewport():
    # A page that echoes its own viewport width; replay must run at the size
    # stored in browser_settings, not the 1280x800 default.
    html = "<div id='w'></div><script>document.getElementById('w').textContent = window.innerWidth</script>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {"mode": "single", "fields": [{"name": "width", "selector": "#w", "take": "text", "transform": "number"}]}
        },
        "browser_settings": {"viewport": {"width": 1520, "height": 700}},
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"] == {"width": 1520}


async def test_extract_waits_for_async_rendered_content():
    # SPA-style: the list root renders ~800ms after load (like Canvas's React
    # app). Extraction must wait for the root to attach instead of racing an
    # empty DOM and silently returning nothing.
    html = """
    <div id='app'></div>
    <script>
    setTimeout(function() {
      var a = document.createElement('div');
      a.className = 'row';
      var s = document.createElement('span');
      s.className = 'name';
      s.textContent = 'Alice';
      a.appendChild(s);
      document.getElementById('app').appendChild(a);
    }, 800);
    </script>
    """
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "root": ".row",
                "fields": [{"name": "name", "selector": ".name", "take": "text"}],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"] == [{"name": "Alice"}]


async def test_stealth_hides_automation_tells():
    # Bot detectors challenge a visit when it looks automated. The stealth init
    # script must have run before page JS, so the page itself observes a normal
    # Chrome: no navigator.webdriver, a non-empty plugin list, window.chrome
    # present. Reading these from inside the page is exactly what a detector does.
    html = """
    <div id='wd'></div><div id='plugins'></div><div id='chrome'></div>
    <script>
    document.getElementById('wd').textContent = String(navigator.webdriver);
    document.getElementById('plugins').textContent = String(navigator.plugins.length);
    document.getElementById('chrome').textContent = String(!!window.chrome);
    </script>
    """
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "fields": [
                    {"name": "webdriver", "selector": "#wd", "take": "text"},
                    {"name": "plugins", "selector": "#plugins", "take": "text", "transform": "number"},
                    {"name": "chrome", "selector": "#chrome", "take": "text"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["webdriver"] == "undefined"  # the loudest tell, gone
    assert result["data"]["plugins"] > 0
    assert result["data"]["chrome"] == "true"


async def test_stealth_reports_a_consistent_gpu():
    # Headless with --disable-gpu falls back to SwiftShader, which no desktop
    # Chrome reports. The substitute has to agree with the rest of the
    # fingerprint: a Windows UA needs a Direct3D/ANGLE renderer, and WebGL1 and
    # WebGL2 must not disagree with each other on one machine.
    html = """
    <div id='v1'></div><div id='r1'></div><div id='v2'></div><div id='r2'></div>
    <script>
    function read(type) {
      const c = document.createElement('canvas').getContext(type);
      const dbg = c.getExtension('WEBGL_debug_renderer_info');
      return [c.getParameter(dbg.UNMASKED_VENDOR_WEBGL),
              c.getParameter(dbg.UNMASKED_RENDERER_WEBGL)];
    }
    const one = read('webgl'), two = read('webgl2');
    document.getElementById('v1').textContent = one[0];
    document.getElementById('r1').textContent = one[1];
    document.getElementById('v2').textContent = two[0];
    document.getElementById('r2').textContent = two[1];
    </script>
    """
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "fields": [
                    {"name": "vendor1", "selector": "#v1", "take": "text"},
                    {"name": "renderer1", "selector": "#r1", "take": "text"},
                    {"name": "vendor2", "selector": "#v2", "take": "text"},
                    {"name": "renderer2", "selector": "#r2", "take": "text"},
                ],
            }
        },
    }
    data = (await replay_workflow(snapshot, {}, None, uuid.uuid4()))["data"]

    assert "SwiftShader" not in data["renderer1"]
    assert "SwiftShader" not in data["renderer2"]
    # Consistent with the Windows UA the same context sends.
    assert "Direct3D11" in data["renderer1"]
    assert "OpenGL" not in data["renderer1"]  # the old macOS-only string
    # WebGL1 and WebGL2 describe the same machine.
    assert data["vendor1"] == data["vendor2"]
    assert data["renderer1"] == data["renderer2"]


async def test_all_selectors_missing_raises_replay_error_with_artifacts(fixture_site_url):
    execution_id = uuid.uuid4()
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "click", "selectors": ["#totally-bogus-selector"]},
        ],
        "extraction": {},
    }
    with pytest.raises(ReplayError) as exc_info:
        await replay_workflow(snapshot, {}, None, execution_id)

    artifact_dir = settings.failures_path / str(execution_id)
    assert artifact_dir.is_dir()
    assert (artifact_dir / "screenshot.png").exists()
    assert (artifact_dir / "page.html").exists()
    assert exc_info.value.artifact_path == str(artifact_dir)


async def test_extract_llm_engine_reads_page_text(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        # The page text must have reached the prompt.
        assert "Aparthotel Stare Miasto" in user
        return {"hotel_name": "Aparthotel Stare Miasto", "price": "BDT 14,049"}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    html = "<h3>Aparthotel Stare Miasto</h3><p>Starting from BDT 14,049</p>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "llm",
                "fields": [
                    {"name": "hotel_name", "description": "the featured hotel name"},
                    {"name": "price", "description": "starting price", "transform": "number"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["hotel_name"] == "Aparthotel Stare Miasto"
    assert result["data"]["price"] == 14049


async def test_llm_engine_falls_back_to_selectors_when_llm_down(fixture_site_url, monkeypatch):
    # engine is "llm" but the LLM is not configured → semantic_extract returns
    # None and replay must fall through to the recorded selector path. Spy on
    # semantic_extract to prove the engine branch actually ran (not that the
    # engine key was silently ignored).
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: False)

    import app.recorder.replay as replay_mod

    real_semantic = replay_mod.semantic_extract
    calls: list = []

    async def spy(page, config):
        result = await real_semantic(page, config)
        calls.append(result)
        return result

    monkeypatch.setattr(replay_mod, "semantic_extract", spy)

    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "engine": "llm",
                "root": ".book-item",
                "fields": [
                    {"name": "title", "selector": ".book-title", "take": "text"},
                    {"name": "price", "selector": ".book-price", "take": "text", "transform": "number"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert calls == [None]  # the llm branch ran and semantic_extract returned None
    assert len(result["data"]) == 3
    assert result["data"][0] == {"title": "Physics 101", "price": 350}


async def test_llm_engine_merges_llm_text_with_selector_attr(monkeypatch):
    # engine=llm with a mixed config: the text field comes from the LLM, the
    # attr:href field comes from the selector path. Proves per-field routing.
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        # The LLM is asked ONLY for text-eligible fields; it never returns "link".
        return {"title": "Physics 101"}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)
    html = "<a class='lnk' href='https://example.com/x'>Physics 101</a>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "llm",
                "fields": [
                    {"name": "title", "description": "the title text"},
                    {"name": "link", "selector": ".lnk", "take": "attr:href"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["title"] == "Physics 101"           # from the LLM (fake returned it)
    assert result["data"]["link"] == "https://example.com/x"  # from the selector (fake did NOT return it)


async def test_llm_engine_merges_list_mode(monkeypatch):
    # List mode per-index merge: each row's text field comes from the LLM,
    # its attr:href field from the selector path.
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {"items": [{"index": 0, "title": "Alpha"}, {"index": 1, "title": "Beta"}]}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)
    html = (
        "<div class='row'><a class='lnk' href='https://ex.com/a'>x</a></div>"
        "<div class='row'><a class='lnk' href='https://ex.com/b'>y</a></div>"
    )
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "engine": "llm",
                "root": ".row",
                "fields": [
                    {"name": "title", "description": "the row title"},
                    {"name": "link", "selector": ".lnk", "take": "attr:href"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"] == [
        {"title": "Alpha", "link": "https://ex.com/a"},
        {"title": "Beta", "link": "https://ex.com/b"},
    ]


async def test_compiled_engine_uses_stored_selectors(monkeypatch):
    # No heal needed: the stored selector resolves. reheal must NOT be called.
    async def boom(*a, **k):
        raise AssertionError("reheal should not run when selectors resolve")

    monkeypatch.setattr(selector_compiler, "reheal", boom)
    html = "<div class='card'><h3 class='title'>Physics 101</h3></div>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "compiled",
                "fields": [{"name": "title", "selectors": [".card .title"], "take": "text"}],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["title"] == "Physics 101"


async def test_compiled_list_floor_normalizes_root_from_roots(monkeypatch):
    # List-mode compiled configs only carry the plural "roots" key. The
    # last-resort semantic_extract floor (via _semantic_list) bails out with
    # `if not config.get("root"): return None`, so without normalization the
    # floor silently never runs for list-mode compiled workflows. Prove the
    # floor is called with a config that has "root" populated from roots[0].
    async def fake_reheal(page, *, mode, root, field):
        return None  # reheal can't fix it either → selector stays null → floor must run

    monkeypatch.setattr(selector_compiler, "reheal", fake_reheal)

    import app.recorder.replay as replay_mod

    captured: list[dict] = []

    async def fake_semantic_extract(page, config):
        captured.append(config)
        return None

    monkeypatch.setattr(replay_mod, "semantic_extract", fake_semantic_extract)

    html = (
        "<div class='card'><h3 class='title'>Alpha</h3></div>"
        "<div class='card'><h3 class='title'>Beta</h3></div>"
    )
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "engine": "compiled",
                "roots": [".card"],
                "fields": [{"name": "title", "selectors": [".missing-selector"], "take": "text"}],
            }
        },
    }
    await replay_workflow(snapshot, {}, None, uuid.uuid4())

    assert len(captured) == 1
    assert captured[0]["root"] == ".card"


async def test_compiled_engine_heals_broken_selector(monkeypatch):
    # Stored selector misses; reheal returns a working one (no DB persistence
    # because workflow_id is None in this test).
    async def fake_reheal(page, *, mode, root, field):
        return [".card .title"]

    monkeypatch.setattr(selector_compiler, "reheal", fake_reheal)
    html = "<div class='card'><h3 class='title'>Physics 101</h3></div>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "compiled",
                "fields": [{"name": "title", "selectors": [".stale-selector"], "take": "text"}],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["title"] == "Physics 101"

import base64

from app.llm import client


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


async def test_images_become_content_parts(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeResponse('{"selectors": ["a"]}')

    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
    img = base64.b64encode(b"pngbytes").decode()
    out = await client.complete_json("sys", "find it", {"type": "object"}, images=[img])

    assert out == {"selectors": ["a"]}
    content = captured["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{img}"


async def test_no_images_keeps_string_content(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
    out = await client.complete_json("sys", "hello", {"type": "object"})

    assert out == {"ok": True}
    assert isinstance(captured["messages"][1]["content"], str)

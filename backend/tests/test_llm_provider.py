from app.llm import client


def test_gemini_client_uses_gemini_base_url_and_key(monkeypatch):
    monkeypatch.setattr(client.settings, "llm_provider", "gemini")
    monkeypatch.setattr(client.settings, "gemini_base_url", "https://gen.example/v1beta/openai/")
    monkeypatch.setattr(client.settings, "gemini_api_key", "aistudio-key")
    built = client._build_client()
    assert str(built.base_url).startswith("https://gen.example/v1beta/openai/")
    assert built.api_key == "aistudio-key"


def test_gemini_model_name_is_configured_model(monkeypatch):
    monkeypatch.setattr(client.settings, "llm_provider", "gemini")
    monkeypatch.setattr(client.settings, "gemini_model", "gemma-4-31b-it")
    assert client._model_name() == "gemma-4-31b-it"


def test_craftx_client_uses_craftx_base_url_and_key(monkeypatch):
    monkeypatch.setattr(client.settings, "llm_provider", "craftx")
    monkeypatch.setattr(client.settings, "craftx_base_url", "https://craftx.example/v1")
    monkeypatch.setattr(client.settings, "craftx_api_key", "craftx-key")
    monkeypatch.setattr(client.settings, "craftx_model", "some-model")
    built = client._build_client()
    assert str(built.base_url).startswith("https://craftx.example/v1")
    assert built.api_key == "craftx-key"
    assert client._model_name() == "some-model"


def test_unknown_provider_defaults_to_gemini(monkeypatch):
    # llama.cpp was removed; any non-craftx provider falls back to the gemini client.
    monkeypatch.setattr(client.settings, "llm_provider", "somethingelse")
    monkeypatch.setattr(client.settings, "gemini_base_url", "https://gen.example/v1beta/openai/")
    monkeypatch.setattr(client.settings, "gemini_api_key", "k")
    monkeypatch.setattr(client.settings, "gemini_model", "m")
    built = client._build_client()
    assert str(built.base_url).startswith("https://gen.example/v1beta/openai/")
    assert client._model_name() == "m"

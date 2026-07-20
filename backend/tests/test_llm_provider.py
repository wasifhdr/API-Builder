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


def test_gemini_embeds_schema_in_prompt(monkeypatch):
    # Google's OpenAI-compat layer is unreliable with response_format json_schema,
    # so gemini must take the same prompt-embedded-schema path as craftx.
    monkeypatch.setattr(client.settings, "llm_provider", "gemini")
    assert client._uses_prompt_schema() is True
    monkeypatch.setattr(client.settings, "llm_provider", "llama")
    assert client._uses_prompt_schema() is False

import importlib
import sys


def test_cache_tiktoken_uses_env_and_caches_models(monkeypatch, tmp_path):
    cache_dir = tmp_path / "custom_cache"
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache_dir))

    calls = {"models": [], "encodings": []}

    def fake_encoding_for_model(model_name):
        calls["models"].append(model_name)
        return object()

    def fake_get_encoding(encoding_name):
        calls["encodings"].append(encoding_name)
        return object()

    monkeypatch.setattr("tiktoken.encoding_for_model", fake_encoding_for_model)
    monkeypatch.setattr("tiktoken.get_encoding", fake_get_encoding)

    sys.modules.pop("cache_tiktoken", None)
    importlib.import_module("cache_tiktoken")

    assert cache_dir.exists() and cache_dir.is_dir()
    assert calls["encodings"] == ["o200k_base", "cl100k_base"]

    sys.modules.pop("cache_tiktoken", None)

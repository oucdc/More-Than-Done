from pathlib import Path

from config import DEFAULT_BASE_URL, DEFAULT_MODEL, Settings


def test_settings_reads_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")

    settings = Settings.from_environment(dotenv_path=tmp_path / "missing.env")

    assert settings.api_key == "test-key"
    assert settings.model == "test-model"
    assert settings.base_url == "https://example.invalid"


def test_settings_uses_documented_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    settings = Settings.from_environment(dotenv_path=tmp_path / "missing.env")

    assert settings.model == DEFAULT_MODEL
    assert settings.base_url == DEFAULT_BASE_URL


def test_settings_repr_does_not_expose_api_key():
    assert "SECRET" not in repr(Settings(api_key="SECRET"))


def test_settings_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    try:
        Settings.from_environment(dotenv_path=tmp_path / "missing.env")
    except ValueError as exc:
        assert "DEEPSEEK_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected missing API key to be rejected")


def test_secret_file_is_ignored_and_example_template_has_no_key_value():
    root = Path(__file__).resolve().parents[1]
    ignored_patterns = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    template = (root / ".env.example").read_text(encoding="utf-8").splitlines()

    assert ".env" in ignored_patterns
    assert any(line.strip() == "DEEPSEEK_API_KEY=" for line in template)

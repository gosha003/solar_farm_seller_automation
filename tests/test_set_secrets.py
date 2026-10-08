from types import SimpleNamespace

import pytest

from solar_outreach import set_secrets


@pytest.mark.parametrize("bad", ["sk-ant-oat01-x", "sk-ant-admin01-x", "\x16", "sk-ant-api03-ключ", "hello"])
def test_rejects_non_api_keys_before_network(monkeypatch, bad):
    monkeypatch.setattr(set_secrets.getpass, "getpass", lambda prompt: bad)
    monkeypatch.setattr(set_secrets.anthropic, "Anthropic", lambda **kw: pytest.fail("сетевой запрос с мусором"))
    with pytest.raises(SystemExit, match="Это не API-ключ"):
        set_secrets.set_anthropic()


@pytest.mark.parametrize("good", ["sk-ant-api03-abc", "sk-ant-usr-1-abc"])
def test_accepts_console_key_formats(monkeypatch, good):
    saved = {}
    monkeypatch.setattr(set_secrets.getpass, "getpass", lambda prompt: good)
    monkeypatch.setattr(set_secrets.anthropic, "Anthropic",
                        lambda **kw: SimpleNamespace(models=SimpleNamespace(list=lambda **k: None)))
    monkeypatch.setattr(set_secrets, "update_env", saved.update)
    set_secrets.set_anthropic()
    assert saved == {"ANTHROPIC_API_KEY": good}

from types import SimpleNamespace

import anthropic
import httpx2
import pydantic
import pytest

from solar_outreach import llm

REQ = httpx2.Request("POST", "https://api.example.invalid/v1/messages")


def status(cls, code):
    return cls("boom", response=httpx2.Response(code, request=REQ), body=None)


def fake_client(monkeypatch, *outcomes):
    """beta.messages.parse raises or returns the outcomes in order."""
    it = iter(outcomes)

    def parse(**kw):
        o = next(it)
        if isinstance(o, BaseException):
            raise o
        return o
    monkeypatch.setattr(llm, "anthropic_client", lambda: SimpleNamespace(beta=SimpleNamespace(
        messages=SimpleNamespace(parse=parse))))


@pytest.mark.parametrize("exc,cls", [
    (anthropic.APIConnectionError(request=REQ), llm.LLMUnavailable),
    (anthropic.APITimeoutError(request=REQ), llm.LLMUnavailable),
    (status(anthropic.RateLimitError, 429), llm.LLMUnavailable),
    (status(anthropic.InternalServerError, 500), llm.LLMUnavailable),
    (status(anthropic.APIStatusError, 529), llm.LLMUnavailable),            # overloaded
    (status(anthropic.BadRequestError, 400), llm.LLMError),                 # e.g. credit balance too low
    (status(anthropic.AuthenticationError, 401), llm.LLMError),
])
def test_api_errors_become_llm_errors(monkeypatch, exc, cls):
    fake_client(monkeypatch, exc)
    with pytest.raises(llm.LLMError) as e:
        llm.answer_questions(["Who are you?"], "en")
    assert type(e.value) is cls and str(e.value) == f"API: {type(exc).__name__}"
    assert issubclass(llm.LLMUnavailable, llm.LLMError)            # callers that catch LLMError keep working


def test_invalid_json_is_retried(monkeypatch):
    bad = pydantic.ValidationError.from_exception_data("Answer", [])
    fake_client(monkeypatch, bad, SimpleNamespace(stop_reason="end_turn", parsed_output=llm.Answer(paragraph="ok")))
    assert llm.answer_questions(["?"], "en").paragraph == "ok"
    fake_client(monkeypatch, bad, bad)
    with pytest.raises(llm.LLMError, match="невалидный JSON"):
        llm.answer_questions(["?"], "en")

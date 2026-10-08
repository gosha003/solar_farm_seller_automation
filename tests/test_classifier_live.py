"""Живой прогон классификатора (Haiku) на наборе из classifier_cases.yaml. Платно (~$0.02), поэтому по флагу:
    LIVE_LLM=1 .venv/Scripts/python -m pytest tests/test_classifier_live.py -q
"""
import os
from pathlib import Path

import pytest
import yaml

from solar_outreach import inbox, llm
from solar_outreach.config import load_config

CASES = yaml.safe_load((Path(__file__).parent / "classifier_cases.yaml").read_text(encoding="utf-8"))
ESCALATE = inbox.ESCALATE


def test_dataset_size_and_languages():
    assert len(CASES) >= 30
    texts = " ".join(c["text"] for c in CASES)
    assert "ы" in texts and "Hello" in texts and "Здравейте" in texts


@pytest.mark.skipif(os.environ.get("LIVE_LLM") != "1", reason="платный живой прогон: LIVE_LLM=1")
@pytest.mark.parametrize("case", CASES, ids=[c["text"][:40] for c in CASES])
def test_live_classification(case):
    load_config()
    listing = {"заголовок": "Фотоволтаична централа 1.5MW", "регион": "Плевен"}
    c = llm.classify(case["text"], listing)
    got = inbox.decide(c.category, c.confidence, case["text"])
    if case["expect"] == "escalate":
        assert got in ESCALATE, (got, c.summary_ru)
    else:
        assert got == case["expect"], (got, c.summary_ru)

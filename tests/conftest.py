import pytest

from solar_outreach import config, sheets

config.LOCAL = None  # tests always run on the public config.yaml, never on the owner's private overrides
config.load_env = lambda *a, **k: None  # and never with the owner's real .env (tokens, DRY_RUN, SPREADSHEET_ID)


class Sheet:
    """In-memory Google Sheet: rows read back as strings, like the API's formatted values.
    fail[(tab, "append"|"update")] = an exception raised once, to test what a pass leaves behind when it dies there."""

    def __init__(self):
        self.tabs = {t: [] for t in sheets.TABS}
        self.fail = {}

    def clean(self, tab, r):
        return {h: "" if r.get(h) is None else str(r.get(h)) for h in sheets.TABS[tab]}

    def read(self, api, sid, tab):
        return [{**r, "_row": i} for i, r in enumerate(self.tabs[tab], 2)]

    def update(self, api, sid, tab, rows):
        if rows and (tab, "update") in self.fail:
            raise self.fail.pop((tab, "update"))
        for r in rows:
            self.tabs[tab][r["_row"] - 2] = self.clean(tab, r)

    def append(self, api, sid, tab, rows):
        if rows and (tab, "append") in self.fail:
            raise self.fail.pop((tab, "append"))
        self.tabs[tab] += [self.clean(tab, r) for r in rows]

    def add(self, tab, *rows):
        self.append(None, None, tab, list(rows))
        return self

    def kv(self, tab):
        return {r[sheets.TABS[tab][0]]: r[sheets.TABS[tab][1]] for r in self.tabs[tab]}


@pytest.fixture
def sheet(monkeypatch):
    s = Sheet()
    for name in ("read", "update", "append"):
        monkeypatch.setattr(sheets, name, getattr(s, name))
    monkeypatch.setattr(sheets, "ensure_tabs", lambda api, sid: None)
    monkeypatch.setattr(sheets, "service", lambda: None)
    monkeypatch.setenv("SPREADSHEET_ID", "test")
    return s

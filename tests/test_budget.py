import base64
import json

from solar_outreach import budget


class Session:
    def __init__(self, costs):
        self.costs, self.acked = costs, []

    def post(self, url, json, timeout):
        if url.endswith(":acknowledge"):
            self.acked += json["ackIds"]
            return self
        self.data = {"receivedMessages": [
            {"ackId": f"a{i}", "message": {"data": base64.b64encode(
                __import__("json").dumps({"costAmount": c, "budgetAmount": 7.0, "currencyCode": "CAD"}).encode()).decode()}}
            for i, c in enumerate(self.costs)]}
        return self

    def json(self):
        return self.data


def test_poweroff_only_at_budget(monkeypatch):
    calls, alerts = [], []
    monkeypatch.setattr(budget.subprocess, "run", lambda cmd, check: calls.append(cmd))
    s = Session([1.4, 3.0])
    assert budget.enforce("sub", alerts.append, s).startswith("3.00 из 7 CAD")
    assert calls == [] and alerts == [] and s.acked == ["a0", "a1"]
    budget.enforce("sub", alerts.append, Session([6.9, 7.2]))
    assert calls == [["sudo", "systemctl", "poweroff"]] and "Выключаю VM" in alerts[0]


def test_no_reports_no_action():
    assert budget.latest("sub", Session([])) is None

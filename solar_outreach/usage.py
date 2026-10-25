"""Claude spend from usage.jsonl (written by llm.log_usage), by day, by ISO week and by calling function.
On the VM: python -m solar_outreach.usage [days]   (default: everything logged)
Dollars come from llm.prices_usd_per_mtok and llm.web_search_usd_per_1000 in config.yaml.
"""
import json
import sys
from collections import defaultdict
from datetime import date, timedelta

from solar_outreach import llm
from solar_outreach.config import load_config


def cost(rec: dict, prices: dict, per_search: float) -> float | None:
    """USD for one record; None when the model has no price in the config."""
    p = prices.get(rec["model"])
    if not p:
        return None
    tokens = (rec["in"] * p[0] + rec["out"] * p[1] + rec["cache_read"] * p[2] + rec["cache_write"] * p[3]) / 1e6
    return tokens + rec["searches"] * per_search / 1000


def summarize(records: list[dict], prices: dict, per_search: float) -> dict:
    """{"day"|"week"|"where": {key: [calls, in, out, cache_read, usd]}, "unpriced": models, "days": calendar days spanned}."""
    out = {k: defaultdict(lambda: [0, 0, 0, 0, 0.0]) for k in ("day", "week", "where")}
    unpriced = set()
    for r in records:
        usd = cost(r, prices, per_search)
        if usd is None:
            unpriced.add(r["model"])
        d = date.fromisoformat(r["ts"][:10])
        y, w, _ = d.isocalendar()
        for kind, key in (("day", d.isoformat()), ("week", f"{y}-W{w:02d}"), ("where", r["where"])):
            row = out[kind][key]
            row[0] += 1
            row[1] += r["in"] + r["cache_write"]
            row[2] += r["out"]
            row[3] += r["cache_read"]
            row[4] += usd or 0.0
    days = sorted(out["day"])
    span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1 if days else 0
    return {**out, "unpriced": sorted(unpriced), "days": span}


def table(title: str, rows: dict) -> None:
    print(f"\n{title}\n{'':<44}{'вызовов':>8}{'вход':>10}{'выход':>9}{'из кэша':>10}{'$':>8}")
    for key, (n, i, o, c, usd) in sorted(rows.items()):
        print(f"{key:<44}{n:>8}{i:>10}{o:>9}{c:>10}{usd:>8.2f}")


def main(days: int = 0) -> None:
    cfg = load_config()
    try:
        lines = llm.USAGE_LOG.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        sys.exit(f"{llm.USAGE_LOG} ещё нет: лог пишется с первого вызова Claude после деплоя")
    records = [json.loads(x) for x in lines if x.strip()]
    if days:
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        records = [r for r in records if r["ts"][:10] >= since]
    s = summarize(records, cfg["llm"]["prices_usd_per_mtok"], cfg["llm"]["web_search_usd_per_1000"])
    if not s["days"]:
        sys.exit("в логе нет записей за этот период")
    table("По дням", s["day"])
    table("По неделям (ISO)", s["week"])
    table("По функциям", s["where"])
    total = sum(r[4] for r in s["day"].values())
    print(f"\nИтого ${total:.2f} за {s['days']} дн.: в среднем ${total / s['days']:.2f} в день, "
          f"${total / s['days'] * 7:.2f} в неделю")
    if s["unpriced"]:
        print(f"Нет цены в config.yaml (посчитаны как $0): {', '.join(s['unpriced'])}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)

"""Google Sheets: the single source of truth. Reads and writes in batches."""
import os
import re
from functools import cache

from googleapiclient.discovery import build

from solar_outreach.config import update_env
from solar_outreach.google_auth import credentials

RETRIES = 3  # googleapiclient backoff on 429/5xx; ponytail: a retried append after a lost 5xx reply may duplicate a row
ITEMS = ["1 действующая", "2 капацитет", "3 док: земля", "3 док: разрешение за строеж", "3 док: договор присъединяване",
         "4 цена", "5 год ввода", "6 выработка/выручка", "7 режим продажи", "8 панели", "8 инверторы",
         "9 земля собств/аренда", "10 актив/SPV, обременения"]


def mark_asked(st: dict, first_letter: bool) -> None:
    """After sending: the first letter asks items 1-4; a reply to the seller's answer asks all the remaining ones."""
    for item in ITEMS:
        if st.get(item) == "не спрошено" and (not first_letter or item.split()[0] in ("1", "2", "3", "4")):
            st[item] = "спрошено"


TABS = {
    "Станции": ["id", "дата найдено", "площадка", "ссылка", "заголовок", "регион", "мощность кВт", "цена EUR", "EUR/кВт",
                "год", "режим", "телефон", "email", "продавец", "канал", "отбор", "скоринг", "приоритет", *ITEMS,
                "статус переписки", "последний контакт", "следующий шаг", "примечание"],
    "Диалоги": ["id станции", "направление", "дата", "канал", "язык", "краткое содержание", "тред Gmail"],
    "Черновики": ["id", "id станции", "дата", "язык", "тип", "канал", "кому", "тема", "текст", "статус",
                  "tg сообщение", "правка владельца", "нарушения правил", "тред Gmail", "in-reply-to", "отправлено"],
    "Ручная очередь": ["id станции", "канал", "ссылка на объявление", "текст", "WhatsApp", "Viber", "статус"],
    "Чёрный список": ["контакт или домен", "причина", "дата"],  # technically undeliverable (bounce) and /blacklist
    "Отказы": ["контакт", "id станции", "причина", "дата", "что написал"],  # never written to automatically (hard rules)
    "Настройки": ["параметр", "значение"],  # key, value: the kv()/set_kv() format
    "_state": ["ключ", "значение"],
}


# «статус переписки» -> row color in the «Станции» tab.
STATUS_COLORS = {
    "квалифицирована": {"red": 0.72, "green": 0.88, "blue": 0.72},
    "ждём ответа": {"red": 1.0, "green": 0.95, "blue": 0.7},
    "эскалация": {"red": 0.96, "green": 0.75, "blue": 0.75},
    "отказ": {"red": 0.96, "green": 0.75, "blue": 0.75},
}


def format_requests(sheet_id: int) -> list[dict]:
    c = col(TABS["Станции"].index("статус переписки") + 1)
    return [{"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [{"sheetId": sheet_id, "startRowIndex": 1}],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": f'=${c}2="{status}"'}]},
                        "format": {"backgroundColor": color}}}}} for status, color in STATUS_COLORS.items()]


@cache  # one client per process: build() on every daemon pass leaks memory (1.5 GB a day)
def service():
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False).spreadsheets()


def col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def create(api, title: str, settings: list[list]) -> str:
    """The spreadsheet with all tabs and headers. Its ID is saved to .env as SPREADSHEET_ID."""
    body = {"properties": {"title": title, "locale": "ru_RU"},
            "sheets": [{"properties": {"title": t, "gridProperties": {"frozenRowCount": 1}}} for t in TABS]}
    created = api.create(body=body, fields="spreadsheetId,sheets.properties").execute(num_retries=RETRIES)
    sid = created["spreadsheetId"]
    stations = next(s["properties"]["sheetId"] for s in created["sheets"] if s["properties"]["title"] == "Станции")
    api.batchUpdate(spreadsheetId=sid, body={"requests": format_requests(stations)}).execute(num_retries=RETRIES)
    data = [{"range": f"'{t}'!A1", "values": [h]} for t, h in TABS.items()]
    data.append({"range": "'Настройки'!A2", "values": settings})
    api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute(num_retries=RETRIES)
    update_env({"SPREADSHEET_ID": sid})
    os.environ["SPREADSHEET_ID"] = sid
    return sid


@cache  # once per process: the daemon used to spend 2 of its 60 reads/min on it every pass
def ensure_tabs(api, sid: str) -> None:
    """Tabs and columns added in later phases are appended to an existing spreadsheet.
    New columns are only added at the end, so existing rows don't shift."""
    have = {s["properties"]["title"] for s in
            api.get(spreadsheetId=sid, fields="sheets.properties.title").execute(num_retries=RETRIES)["sheets"]}
    missing = [t for t in TABS if t not in have]
    if missing:
        api.batchUpdate(spreadsheetId=sid, body={"requests": [
            {"addSheet": {"properties": {"title": t, "gridProperties": {"frozenRowCount": 1}}}} for t in missing]}).execute(
            num_retries=RETRIES)
    api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": [
        {"range": f"'{t}'!A1", "values": [h]} for t, h in TABS.items()]}).execute(num_retries=RETRIES)


def read(api, sid: str, tab: str) -> list[dict]:
    """Rows as dicts; `_row` = the row number in the sheet (for updates)."""
    rows = api.values().get(spreadsheetId=sid, range=f"'{tab}'").execute(num_retries=RETRIES).get("values", [])
    if not rows:
        return []
    head = rows[0]
    return [{**dict(zip(head, r + [""] * (len(head) - len(r)))), "_row": i} for i, r in enumerate(rows[1:], 2)]


def cells(r: dict, head: list[str]) -> list:
    """A row for USER_ENTERED (it keeps dates and numbers typed). Phone-like strings ("+359...", "0888...",
    "+359..., +359...") get a leading ' so Sheets stores them as text instead of a number that loses the + or
    the 0, or garbles a list. So do strings starting with = - @: scraped titles («=== ПРОДАВАМ ФЕЦ ===») and seller
    text must never become formulas. The ' is not part of the value: reads return the string without it."""
    vals = ["" if r.get(h) is None else r.get(h) for h in head]
    return ["'" + v if isinstance(v, str) and re.match(r"[+=@-]|0\d", v) else v for v in vals]


def update(api, sid: str, tab: str, rows: list[dict]) -> None:
    """Overwrite whole rows (by `_row`) in a single batch request."""
    head = TABS[tab]
    data = [{"range": f"'{tab}'!A{r['_row']}", "values": [cells(r, head)]} for r in rows]
    if data:
        api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "USER_ENTERED", "data": data}).execute(
            num_retries=RETRIES)


def kv(api, sid: str, tab: str) -> dict[str, dict]:
    """«_state» / «Настройки»: {key: string}. Columns: first = key, second = value."""
    return {r[TABS[tab][0]]: r for r in read(api, sid, tab)}


def set_kv(api, sid: str, tab: str, values: dict) -> None:
    if not values:
        return
    key_col, val_col = TABS[tab]
    existing = kv(api, sid, tab)
    upd = [{**existing[k], val_col: v} for k, v in values.items() if k in existing]
    update(api, sid, tab, upd)
    append(api, sid, tab, [{key_col: k, val_col: v} for k, v in values.items() if k not in existing])


def append(api, sid: str, tab: str, rows: list[dict]) -> None:
    if rows:
        values = [cells(r, TABS[tab]) for r in rows]
        api.values().append(spreadsheetId=sid, range=f"'{tab}'!A1", valueInputOption="USER_ENTERED",
                            insertDataOption="INSERT_ROWS", body={"values": values}).execute(num_retries=RETRIES)


def next_id(ids, prefix: str = "D") -> str:
    """Next id after the largest existing one («D007» -> «D008»). A count would reuse a live id after a deleted row
    or a draft counted twice in one pass, and by_id would then hide one of the two drafts."""
    n = max([int(re.sub(r"\D", "", i) or 0) for i in ids] + [0]) + 1
    return f"{prefix}{n:03d}"

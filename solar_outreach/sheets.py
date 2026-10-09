"""Google Sheets: единственный источник правды (раздел 6). Чтение и запись пачками."""
import os

from googleapiclient.discovery import build

from solar_outreach.config import update_env
from solar_outreach.google_auth import credentials

ITEMS = ["1 действующая", "2 капацитет", "3 док: земля", "3 док: разрешение за строеж", "3 док: договор присъединяване",
         "4 цена", "5 год ввода", "6 выработка/выручка", "7 режим продажи", "8 панели", "8 инверторы",
         "9 земля собств/аренда", "10 актив/SPV, обременения"]
TABS = {
    "Станции": ["id", "дата найдено", "площадка", "ссылка", "заголовок", "регион", "мощность кВт", "цена EUR", "EUR/кВт",
                "год", "режим", "телефон", "email", "продавец", "канал", "отбор", "скоринг", "приоритет", *ITEMS,
                "статус переписки", "последний контакт", "следующий шаг", "примечание"],
    "Диалоги": ["id станции", "направление", "дата", "канал", "язык", "краткое содержание", "тред Gmail"],
    "Черновики": ["id", "id станции", "дата", "язык", "тип", "канал", "кому", "тема", "текст", "статус",
                  "tg сообщение", "правка владельца", "нарушения правил", "тред Gmail", "in-reply-to", "отправлено"],
    "Ручная очередь": ["id станции", "канал", "ссылка на объявление", "текст", "WhatsApp", "Viber", "статус"],
    "Чёрный список": ["контакт или домен", "причина", "дата"],  # технически недоставляемые (bounce) и /blacklist
    "Отказы": ["контакт", "id станции", "причина", "дата", "что написал"],  # не писать автоматически (раздел 4)
    "Настройки": ["параметр", "значение"],  # ключ, значение: формат kv()/set_kv()
    "_state": ["ключ", "значение"],
}


# «статус переписки» -> цвет строки во вкладке «Станции» (раздел 6).
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


def service():
    return build("sheets", "v4", credentials=credentials(), cache_discovery=False).spreadsheets()


def col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def create(api, title: str, settings: list[list]) -> str:
    """Таблица со всеми вкладками и заголовками. ID сохраняется в .env как SPREADSHEET_ID."""
    body = {"properties": {"title": title, "locale": "ru_RU"},
            "sheets": [{"properties": {"title": t, "gridProperties": {"frozenRowCount": 1}}} for t in TABS]}
    created = api.create(body=body, fields="spreadsheetId,sheets.properties").execute()
    sid = created["spreadsheetId"]
    stations = next(s["properties"]["sheetId"] for s in created["sheets"] if s["properties"]["title"] == "Станции")
    api.batchUpdate(spreadsheetId=sid, body={"requests": format_requests(stations)}).execute()
    data = [{"range": f"'{t}'!A1", "values": [h]} for t, h in TABS.items()]
    data.append({"range": "'Настройки'!A2", "values": settings})
    api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute()
    update_env({"SPREADSHEET_ID": sid})
    os.environ["SPREADSHEET_ID"] = sid
    return sid


def ensure_tabs(api, sid: str) -> None:
    """Вкладки и колонки, появившиеся в новых фазах, дописываются в уже созданную таблицу.
    Новые колонки добавляются только в конец, поэтому старые строки не съезжают."""
    have = {s["properties"]["title"] for s in api.get(spreadsheetId=sid, fields="sheets.properties.title").execute()["sheets"]}
    missing = [t for t in TABS if t not in have]
    if missing:
        api.batchUpdate(spreadsheetId=sid, body={"requests": [
            {"addSheet": {"properties": {"title": t, "gridProperties": {"frozenRowCount": 1}}}} for t in missing]}).execute()
    api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": [
        {"range": f"'{t}'!A1", "values": [h]} for t, h in TABS.items()]}).execute()


def read(api, sid: str, tab: str) -> list[dict]:
    """Строки как dict; `_row` = номер строки в листе (для update)."""
    rows = api.values().get(spreadsheetId=sid, range=f"'{tab}'").execute().get("values", [])
    if not rows:
        return []
    head = rows[0]
    return [{**dict(zip(head, r + [""] * (len(head) - len(r)))), "_row": i} for i, r in enumerate(rows[1:], 2)]


def update(api, sid: str, tab: str, rows: list[dict]) -> None:
    """Перезаписать строки целиком (по `_row`), одним batch-запросом."""
    head = TABS[tab]
    data = [{"range": f"'{tab}'!A{r['_row']}", "values": [["" if r.get(h) is None else r.get(h) for h in head]]}
            for r in rows]
    if data:
        api.values().batchUpdate(spreadsheetId=sid, body={"valueInputOption": "USER_ENTERED", "data": data}).execute()


def kv(api, sid: str, tab: str) -> dict[str, dict]:
    """«_state» / «Настройки»: {ключ: строка}. Колонки: первая = ключ, вторая = значение."""
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
        head = TABS[tab]
        values = [["" if r.get(h) is None else r.get(h) for h in head] for r in rows]
        api.values().append(spreadsheetId=sid, range=f"'{tab}'!A1", valueInputOption="USER_ENTERED",
                            insertDataOption="INSERT_ROWS", body={"values": values}).execute()

"""The owner's Telegram bot. No webhooks: getUpdates on every loop run, and between runs
the daemon on the VM waits for updates with long polling (wait), so it answers within seconds.

Messages are Telegram HTML built with the helpers below. Every value that comes from a seller, a listing, the sheet
or a model goes through esc() (or a helper that escapes): sellers write anything. If Telegram still rejects the
markup, the message goes out as plain text, so a stray «<» never loses a notice. One layout for every message:
a first line «emoji <b>Title</b> · context», then short sections with a bold heading, links with a short label instead
of raw URLs, letters and seller text in a collapsible quote, commands in <code> so they copy with a tap.
"""
import html
import os
import re

import requests

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000  # Telegram allows 4096 characters per message


class TelegramError(Exception):
    pass


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=False)


def b(v) -> str:
    return f"<b>{esc(v)}</b>"


def i(v) -> str:
    return f"<i>{esc(v)}</i>"


def code(v) -> str:
    return f"<code>{esc(v)}</code>"


def link(url: str, label: str) -> str:
    """A short label instead of a long URL; anything that isn't a web link stays plain text."""
    url = (url or "").strip()
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>' if url.startswith(("http://", "https://")) \
        else esc(label)


def quote(text: str, expandable: bool = True) -> str:
    """Seller text or a letter: a quote, collapsed when long so the card stays short."""
    text = (text or "").strip()
    return f"<blockquote{' expandable' if expandable and len(text) > 300 else ''}>{esc(text)}</blockquote>" if text else ""


def num(v) -> str:
    """«1160000» -> «1 160 000», «1499,6» -> «1 499,6»; anything that isn't a number («неизвестно») stays as it is."""
    s = str(v if v is not None else "").strip()
    try:
        x = float(s.replace(" ", "").replace(",", "."))
    except ValueError:
        return s
    whole = f"{abs(x):,.0f}".replace(",", " ") if x == int(x) else f"{abs(x):,.1f}".replace(",", " ").replace(".", ",")
    return ("-" if x < 0 else "") + whole


def head(emoji: str, title: str, *context) -> str:
    """The first line of every message: «📝 <b>Title</b> · S020 · D012»."""
    return f"{emoji} {b(title)}" + "".join(f" · {esc(c)}" for c in context if c)


def section(title: str, lines) -> str:
    """A bold heading and bullet lines. Lines are markup already (escape their values)."""
    lines = [x for x in lines if x]
    return f"{b(title)}\n" + "\n".join(f"• {x}" for x in lines) if lines else ""


def join(*parts) -> str:
    """Paragraphs of a message, empty ones dropped."""
    return "\n\n".join(p for p in parts if p)


LINK = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.S)


def plain(markup: str) -> str:
    """The text without tags, for the plain fallback and for email; a link keeps its address: «тред (https://...)»."""
    markup = LINK.sub(lambda m: f"{m[2]} ({m[1]})", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def call(method: str, **params) -> dict | list:
    """The token is part of the URL, so requests exceptions (URL in the text) never escape: only method and description."""
    try:
        r = requests.post(API.format(token=os.environ["TELEGRAM_BOT_TOKEN"], method=method), json=params,
                          timeout=30 + params.get("timeout", 0))  # long polling holds the connection for `timeout` seconds
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise TelegramError(f"{method}: {type(e).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"{method}: {r.status_code} {data.get('description', '')}")
    return data["result"]


QUOTE_TAG = re.compile(r"<blockquote( expandable)?>|</blockquote>")


def _cut(line: str, budget: int) -> int:
    """Where to cut an overlong line: at a space, never inside a tag or an «&...;» entity."""
    cut = line.rfind(" ", 0, budget)
    cut = cut if cut > budget // 2 else budget
    for opener, closer in (("&", ";"), ("<", ">")):
        at = line.rfind(opener, 0, cut)
        if at > 0 and closer not in line[at:cut]:
            cut = at
    return cut


def split(text: str) -> list[str]:
    """Messages over the limit go out in parts, cut between lines (an overlong line at a space). A quote cut in two
    is closed and reopened, so every part is valid markup and no text is lost."""
    if len(text) <= LIMIT:
        return [text]
    budget, units = LIMIT - 60, []
    for line in text.split("\n"):
        while len(line) > budget:
            cut = _cut(line, budget)
            units.append(line[:cut])
            line = line[cut:].lstrip(" ")
        units.append(line)
    parts, cur, opened = [], "", ""
    for u in units:
        if cur and cur != opened and len(cur) + len(u) + 1 > budget:
            parts.append(cur + ("</blockquote>" if opened else ""))
            cur = opened
        cur = cur + u if cur == opened else f"{cur}\n{u}"
        for m in QUOTE_TAG.finditer(u):
            opened = "" if m.group(0) == "</blockquote>" else m.group(0)
    parts.append(cur)
    return [x for x in parts if plain(x).strip()]


def _send(text: str, buttons) -> int:
    params = {"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
    try:
        return call("sendMessage", **params)["message_id"]
    except TelegramError as e:
        if "parse" not in str(e).lower() and "entit" not in str(e).lower():
            raise
        params.pop("parse_mode")  # markup Telegram rejects: the notice still arrives, as plain text
        params["text"] = plain(text)[:LIMIT]
        return call("sendMessage", **params)["message_id"]


def send(text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int:
    """HTML text (see the module docstring); a long one goes in parts, the buttons under the last. Returns its id."""
    parts = split(text)
    for part in parts[:-1]:
        _send(part, None)
    return _send(parts[-1], buttons)


def try_send(text: str) -> None:
    """A notice whose loss is harmless (📤 sent, ⛔ skipped): a Telegram blip must not abort a pass after letters went out."""
    try:
        send(text)
    except Exception:  # Telegram down or not configured on this machine
        pass


def urgent(text: str) -> None:
    """Urgent: Telegram right away; if Telegram is down, an email to the owner prefixed [SOLAR]."""
    try:
        send(text)
    except TelegramError:
        flat = plain(text)
        email_owner(flat.split("\n", 1)[0][:80], flat)


def email_owner(subject: str, text: str) -> None:
    from solar_outreach import gmail_client
    from solar_outreach.config import load_config
    cfg = load_config()
    gmail_client.send(gmail_client.service(), cfg["owner"]["email"], f"{cfg['notify']['email_prefix']} {subject}", text)


def updates(offset: int) -> list[dict]:
    """Only updates from the owner's chat; everything else is dropped silently (anyone can find the bot and write to it)."""
    owner = str(os.environ["TELEGRAM_CHAT_ID"])
    out = []
    for u in call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"]):
        chat = (u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat", {})
        sender = (u.get("callback_query") or u.get("message") or {}).get("from", {})
        u["_owner"] = str(chat.get("id")) == owner and str(sender.get("id")) == owner
        out.append(u)
    return out


def wait(offset: int, seconds: int) -> None:
    """Long polling: return as soon as the bot has an update (or after `seconds`). Updates are not acknowledged:
    the next loop run picks them up with the same offset."""
    call("getUpdates", offset=offset, timeout=seconds, limit=1, allowed_updates=["message", "callback_query"])


def answer_callback(callback_id: str, text: str = "") -> None:
    """Telegram accepts an answer to a button tap for only a few seconds; with polling every N minutes it is already "too old".
    It is cosmetic (removes the spinner on the button), so the error is ignored."""
    try:
        call("answerCallbackQuery", callback_query_id=callback_id, text=text)
    except TelegramError:
        pass


COMMANDS = {"stats": "статистика", "status": "карточка станции: /status S019", "queue": "ручная очередь",
            "digest": "дайджест сейчас", "pause": "остановить отправку продавцам", "resume": "возобновить отправку",
            "auto": "/auto on или /auto off", "blacklist": "больше не писать: /blacklist email",
            "sent": "я сам отправил: /sent S020", "note": "ответ продавца из WhatsApp: /note S020 текст",
            "brief": "сводка директору: /brief S020"}


def set_commands() -> None:
    """The "/" menu in Telegram (setMyCommands): one-off, called from set_secrets."""
    call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS.items()])


def draft_buttons(draft_id: str) -> list[list[tuple[str, str]]]:
    return [[("✅ Одобрить", f"a:{draft_id}"), ("✏️ Править", f"e:{draft_id}"), ("⏭ Пропустить", f"s:{draft_id}")]]

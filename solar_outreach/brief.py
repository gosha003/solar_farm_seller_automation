"""Director briefs: an object goes to the director only when its documents prove it is ready for official negotiations.

A pass (systemd timer, twice a day at 12:55 and 22:55 Toronto time):
  1. documents.refresh reads new files in the candidates' Drive folders into «Документы».
  2. gate() checks in code, from those records only, the strict document set: a project needs a building permit that
     is in force (3 years to start building, a site-opening protocol after that), a final grid contract whose deadline
     has not passed (annexes count), proof the connection fee was paid, and a land title or a right to build; an
     operating plant needs the use permit (Акт 16), a grid contract, land, 12 months of production and a power sale
     contract. Capacity comes from the documents and must fit the criteria (owner exceptions pass).
  3. Ready -> Claude writes the brief, a second call fact-checks it against the records, one revision -> a card to the
     owner; his ✅ emails the director with the key documents attached. Nothing reaches the director without the ✅,
     and the gate runs again at that moment (a deadline may have passed meanwhile).
  4. Not ready -> the owner learns why, once per change. What the seller can supply is requested inside the existing
     conversation: Claude reads the history and declines when asking is out of place (documents promised only after
     an NDA or a deposit, a recent unanswered request); the letter is an ordinary draft (hard rules, approval mode,
     the seller's Gmail thread or «Ручная очередь»). A conversation the owner leads always gets a card, never an
     automatic letter, and keeps its «ведёт владелец» status.
  5. The director's replies to a brief are handled by director.py.

    python -m solar_outreach.brief           # the timer's pass
    python -m solar_outreach.brief S0xx      # one station now, even if nothing changed
"""
import hashlib
import json
import os
import re
import sys
from datetime import date
from email.utils import parseaddr
from pathlib import Path
from typing import Literal

import pydantic

from solar_outreach import (approval, documents as docs_, drafts, drive, gmail_client as gmail, inbox, llm, notify, rules,
                            sheets)
from solar_outreach.config import load_config, load_env, single_run
from solar_outreach.notify import code, esc, head, join, link, section  # notify.b: «b» is a Brief here
from solar_outreach.documents import (ACCESS, ANNEX, BUILD_RIGHT, GRID, INVOICE, LAND, LEASE, PERMIT, POWER_SALE,
                                      PRODUCTION, PROTOCOL, UNREAD, USE_PERMIT)

DOC_REQUEST = "запрос документов"  # draft type; skipping its card never pauses the dialog
DIRECTOR_REQUEST = f"{DOC_REQUEST} по ответу директора"
DIRECTOR_LABEL = "SolarBG/директор"  # briefs and the director's replies; never SolarBG, so never the seller pipeline
CLOSED = ("новая", "черновик", "не писать", "отказ", "пропущено владельцем", "перенаправлено")
SEEN = "drive: обработано"  # «_state» key: the seller Drive file ids already handled
REQ = " → запросить: "  # a «Сводки» problem line: what is wrong, then what the seller can send
# disclaimers and first-person hedging the owner never wants in a brief; checked in code after the model
HEDGE = re.compile(r"\bя\b|\bмне\b|\bмо[йяеи]\b|\bмоё\b|не являюсь|\bне юрист|на мой взгляд|по моему мнению"
                   r"|это (лишь |только )?(мо[её]|наше) мнение|\bИИ\b|искусственн\w* интеллект|нейросет\w*"
                   r"|языков\w* модел\w*|не является (юридическ|финансов|инвестиционн)\w* (консультац|совет)\w*"
                   r"|не могу гарантир\w*", re.I)


def cleared(d: dict) -> bool:
    """A document request that may enter a conversation the owner leads: the director asked for it, or the owner
    approved it on its card (it was shown to him: «tg сообщение» is set)."""
    return d["тип"] == DIRECTOR_REQUEST or d["тип"] == DOC_REQUEST and bool(d.get("tg сообщение"))


def _d(s: str) -> date | None:
    """ISO, or what a model or a seller writes anyway: 15.03.2024, 4.12.23, «15.03.2024 г.»."""
    s = str(s).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b", s)
    try:
        return date(int(m[3]) + (2000 if len(m[3]) == 2 else 0), int(m[2]), int(m[1])) if m else None
    except ValueError:
        return None


def _years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # 29 February
        return d.replace(year=d.year + n, day=28)


DATES = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}")


def _num(s: str) -> str:
    """The first number of a document reference, dates removed: «№ 7/01.02.2025» -> «7»."""
    found = re.findall(r"\d+", DATES.sub(" ", s or ""))
    return found[0].lstrip("0") if found else ""


def same_ref(number: str, ref: str) -> bool:
    """«12» belongs to «РС № 12/15.03.2024», «1234567» to «към договор от 11.01.2024 № 1234567»; date digits never match."""
    main = _num(number)
    return bool(main) and main in {x.lstrip("0") for x in re.findall(r"\d+", DATES.sub(" ", ref or ""))}


OPERATING_ANSWER = re.compile(r"получен: (да|работ|действ|в эксплуатац|присъедин)", re.I)


def is_operating(st: dict, docs: list[dict]) -> bool:
    """A use permit for the plant, or the seller or the listing says it runs. Unknown means a project: asking an operating
    plant for a protocol is harmless, asking a project for 12 months of production is nonsense."""
    if any(d["type"] == USE_PERMIT for d in docs):
        return True
    if st.get("отбор", "").startswith("проект"):
        return False
    return bool(OPERATING_ANSWER.match(st.get("1 действующая", "")) or drafts.OPERATING.search(st.get("заголовок", "")))


def permit_check(p: dict, protocols: list[dict], single: bool, today: date, days: int) -> tuple[str, str, str]:
    """(problem, request, deadline line). ЗУТ чл. 153: the permit lapses if building hasn't started within 3 years of
    entering into force (a re-certification restarts the count), or the rough build isn't finished 5 years after the
    start. Only a protocol dated inside those 3 years counts. Problem texts carry dates, never a day count, so the same
    situation reads the same tomorrow."""
    start = _d(p.get("in_force", ""))
    if not start:
        return f"{p['title']}: дата вступления в силу не заполнена", f"{p['title']} с отметкой о вступлении в силу", ""
    end = _years(max(start, _d(p.get("recertified", "")) or start), 3)
    began = [b for x in protocols if same_ref(p["number"], x.get("refers_to", "")) or single and not x.get("refers_to")
             if (b := _d(x.get("date", ""))) and start <= b <= end]
    if began:
        rough = _years(min(began), 5)
        line = f"{p['title']}: строительство начато {min(began):%d.%m.%Y}, грубый строеж до {rough:%d.%m.%Y}"
        if rough < today:
            return f"{p['title']}: 5 лет с начала строительства прошли {rough:%d.%m.%Y}, разрешение утратило силу", "", line
        if (rough - today).days < days:
            return (f"{p['title']}: грубый строеж нужно закончить до {rough:%d.%m.%Y}, меньше {days} дн.",
                    f"акт о завершении грубого строежа или документ о продлении по {p['title']}", line)
        return "", "", line
    line = f"{p['title']}: начать строительство до {end:%d.%m.%Y}"
    ask = f"протокол об открытии строительной площадки по {p['title']}"
    if end < today:
        return f"{p['title']}: 3 года на начало строительства истекли {end:%d.%m.%Y}, протокола открытия площадки нет", ask, line
    if (end - today).days < days:
        return (f"{p['title']}: теряет силу {end:%d.%m.%Y}, если строительство не начато (меньше {days} дн.), "
                "протокола открытия площадки нет", f"{ask} или подтверждение презаверки разрешения", line)
    return "", "", line


def grid_check(g: dict, annexes: list[dict], today: date, days: int) -> tuple[str, str, str]:
    """Deadlines merged by kind: the contract's, each replaced by the newest annex signed after it that moves that
    kind. The binding one is the plant's commissioning, else the earliest; the others become lines for the brief."""
    signed = _d(g.get("date", ""))
    merged = {dl.get("kind") or dl.get("what", ""): dl for dl in g.get("deadlines", []) if _d(dl.get("date", ""))}
    mine = sorted((a for a in annexes if same_ref(g["number"], a.get("refers_to", ""))
                   and (not signed or (_d(a.get("date", "")) or date.min) >= signed)),
                  key=lambda a: _d(a.get("date", "")) or date.min)
    for a in mine:
        merged.update({dl.get("kind") or dl.get("what", ""): {**dl, "by": a["title"]}
                       for dl in a.get("deadlines", []) if _d(dl.get("date", ""))})
    if not merged:
        return f"{g['title']}: срок ввода в документах не найден", f"{g['title']} полностью, со всеми приложениями и анексами", ""
    main = merged.get("ввод объекта") or min(merged.values(), key=lambda dl: _d(dl["date"]))
    due = _d(main["date"])
    line = "; ".join(f"{g['title']}: {dl.get('what') or k} до {_d(dl['date']):%d.%m.%Y}"
                     + (f" по {dl['by']}" if dl.get("by") else "") for k, dl in merged.items())
    ask = f"анекс о продлении сроков по {g['title']}, если он заключён"
    if due < today:
        return f"{g['title']}: срок ввода {due:%d.%m.%Y} истёк, анекса о продлении нет", ask, line
    if (due - today).days < days:
        return f"{g['title']}: срок ввода {due:%d.%m.%Y}, меньше {days} дн.", ask, line
    return "", "", line


def _float(v) -> float:
    """Sheet numbers come back locale-formatted: «1 499,6»."""
    try:
        return float(re.sub(r"\s", "", str(v)).replace(",", "."))
    except ValueError:
        return 0.0


def unique(docs: list[dict]) -> list[dict]:
    """One record per document number (the same permit scanned twice, a copy uploaded by hand): the most complete
    one, so a stamp or a signature on any copy counts and the problem is listed once."""
    best = {}
    for d in docs:
        key = _num(d.get("number", "")) or d["title"]
        score = (d.get("executed", True), bool(d.get("in_force")), len(d.get("deadlines") or []), len(json.dumps(d, ensure_ascii=False)))
        if key not in best or score > best[key][0]:
            best[key] = (score, d)
    return [d for _, d in best.values()]


def capacity(st: dict, docs: list[dict], operating: bool) -> float:
    """Installed kWp from the permits (projects) or use permits (plants), each permit once by its number (the same
    permit sent twice counts once); else the listing's figure."""
    src = USE_PERMIT if operating else PERMIT
    seen = {_num(d.get("number", "")) or d["title"]: d["kwp"] for d in docs if d["type"] == src and d.get("kwp")}
    return round(sum(seen.values()), 2) if seen else _float(st.get("мощность кВт", ""))


def gate(st: dict, docs: list[dict], cfg: dict, today: date) -> tuple[list[tuple[str, str]], dict]:
    """Strict, in code, from the document records only. Returns ([(problem, request or "")], facts for the brief)."""
    days = cfg["director"]["min_days_left"]
    read = [d for d in docs if d["type"] != UNREAD]
    ok = [d for d in read if d.get("executed", True)]
    def of(*types, signed=True):
        return [d for d in (ok if signed else read) if d["type"] in types]
    problems, lines = [], []
    def unsigned(d):  # an unsigned copy proves nothing, but its terms and deadlines still tell what is wrong
        if not d.get("executed", True):
            problems.append((f"{d['title']}: в присланной копии нет подписей и печатей", f"подписанный экземпляр: {d['title']}"))
    def need(types, found, problem, request):
        if found:
            return
        copies = [d for d in of(*types, signed=False) if not d.get("executed", True)]
        for d in copies:  # the missing piece exists, only unsigned: ask for the signed copy, not for the document
            unsigned(d)
        if not copies:
            problems.append((problem, request))
    land = ((LAND, BUILD_RIGHT, LEASE),
            "нет документа на землю: нотариального акта на собственность, права строить или договора аренды",
            "нотариальный акт на землю или на право строить, либо договор аренды с отметкой о регистрации")
    operating = is_operating(st, docs)
    if operating:
        need((USE_PERMIT,), of(USE_PERMIT), "нет разрешения на ввод в эксплуатацию (Акт 16)",
             "разрешение на ввод в эксплуатацию или удостоверение о вводе")
        need((GRID, ACCESS), of(GRID, ACCESS), "нет договора с сетью", "договор о присоединении и договор доступа к сети")
        need(land[0], of(*land[0]), *land[1:])
        need((PRODUCTION,), any((d.get("months") or 0) >= 12 for d in of(PRODUCTION)),
             "нет данных о выработке за 12 месяцев", "данные о выработке по месяцам за последние 12 месяцев")
        need((POWER_SALE,), of(POWER_SALE), "нет договора продажи электроэнергии", "договор продажи электроэнергии")
    else:
        permits, protocols = unique(of(PERMIT, signed=False)), of(PROTOCOL)
        if not permits:
            problems.append(("нет разрешения на строительство", "разрешение на строительство с отметкой о вступлении в силу"))
        for p in permits:
            unsigned(p)
            problem, request, line = permit_check(p, protocols, len(permits) == 1, today, days)
            problems += [(problem, request)] if problem else []
            lines += [line] if line else []
        grids = unique(of(GRID, signed=False))
        if not grids:
            problems.append(("нет окончательного договора о присоединении",
                             "окончательный договор о присоединении со всеми приложениями"))
        for g in grids:
            unsigned(g)
            problem, request, line = grid_check(g, of(ANNEX), today, days)
            problems += [(problem, request)] if problem else []
            lines += [line] if line else []
        if not any(d.get("paid_confirmed") for d in ok):
            problems.append(("оплата присоединения не подтверждена платёжным документом"
                             + (": есть только счета" if of(INVOICE, signed=False) else ""),
                             "платёжные поручения по оплате присоединения"))
        need(land[0], of(*land[0]), *land[1:])
    kwp, c = capacity(st, docs, operating), cfg["criteria"]
    if "исключение владельца" not in st.get("отбор", "") and not c["min_kwp"] <= kwp <= c["max_kwp"]:
        problems.append((f"мощность по документам {kwp:g} kWp вне критериев {c['min_kwp']}–{c['max_kwp']} kWp", ""))
    return problems, {"operating": operating, "kwp": kwp, "deadlines": lines,
                      "unreadable": [d["file"] for d in docs if d["type"] == UNREAD]}


class Risk(pydantic.BaseModel):
    level: Literal["высокий", "средний", "низкий"]
    text: str


class Issue(pydantic.BaseModel):
    text: str       # why official negotiations are premature
    request: str    # what the seller can send to fix it, "" if nothing


class Brief(pydantic.BaseModel):  # the limits keep it a brief: the owner's examples are five short paragraphs
    title: str = pydantic.Field(description="at most 70 characters, no station id: «ФЭС 1,5 MWp на крышах, Плевен»")
    overview: str = pydantic.Field(description="at most 5 sentences: what, where, capacity, what exactly is sold, "
                                               "price and €/kWp, who sells")
    price_eur: float | None = pydantic.Field(description="the seller's total asking price for what is sold, as stated; "
                                                         "null if never stated")
    confirmed: list[str] = pydantic.Field(description="at most 7 one-line facts the documents prove, each naming its "
                                                      "document by number and date")
    seller_only: list[str] = pydantic.Field(description="at most 5 one-line facts known only from the seller")
    risks: list[Risk] = pydantic.Field(description="at most 6, the most material, one or two sentences each")
    opinion: str = pydantic.Field(description="at most 5 sentences: a clear verdict and the terms to negotiate")
    next_steps: list[str] = pydantic.Field(description="at most 5 short steps to settle before signing")
    attach: list[str] = pydantic.Field(description="up to 8 file_id of the key documents, most important first")
    blocking: list[Issue] = pydantic.Field(description="what makes official negotiations premature; empty when ready")


class Check(pydantic.BaseModel):
    claim: str
    problem: Literal["неверно", "не подтверждено", "вводит в заблуждение", "упущен важный факт", "оговорка"]
    fix: str


class Review(pydantic.BaseModel):
    issues: list[Check]


STYLE = """Style: Russian, plain business prose for the company's director, short declarative sentences, dates as \
DD.MM.YYYY, numbers with units. Impersonal voice: no first person singular, no «на мой взгляд», «по моему мнению», \
«это моё мнение», «я не юрист», no disclaimers of any kind and no mention of AI, models or automation. State \
assessments as assessments of the company's analysis, firmly."""

COMPOSE = """Today is {today}. Write a brief for the director about the object below, so he can decide whether to \
start official negotiations with the owner. The code has already checked the mandatory document set; check the rest: \
does the company being sold actually hold the permits, the grid contract and the land right; do plots, capacities and \
holders match across documents; does anything the seller says contradict the documents. Put in blocking only what \
makes official negotiations premature, each with what the seller can send to resolve it.
{style}
Rules: every item in confirmed names its document by type, number and date; what only the seller says goes to \
seller_only; never state anything the sources don't support. overview: what the object is, where, capacity, what \
exactly is sold (shares or assets, land), the asking price and €/kWp, who sells. risks: concrete, with level. \
opinion: one paragraph, a clear verdict and the terms to negotiate. attach: up to 8 file_id of the key documents \
(permits and their protocols, grid contract and annexes, payment proof, land, Акт 16, production, power sale). \
It is a brief: one line per list item, the whole text under about 4000 characters.

<station>{station}</station>
<history>{history}</history>
<deadlines_computed>{deadlines}</deadlines_computed>
<unreadable_files>{unreadable}</unreadable_files>
<documents>{documents}</documents>{fix}"""

VERIFY = """Today is {today}. Fact-check the brief below against the sources. Report only real problems: a number, \
date, name or document id that disagrees with the sources; a claim they don't support; misleading wording; a fact \
the director needs that is missing; and any hedging, disclaimer or first-person phrase (problem «оговорка»). Give \
each fix in Russian. No problems -> empty list.

<station>{station}</station>
<history>{history}</history>
<deadlines_computed>{deadlines}</deadlines_computed>
<documents>{documents}</documents>
<brief>{brief}</brief>"""


def sources(st: dict, docs: list[dict], history: list[dict], facts: dict) -> dict:
    station = {k: v for k, v in st.items() if v and not k.startswith("_") and k not in ("скоринг", "приоритет")}
    return {"station": json.dumps(station, ensure_ascii=False),
            "history": "\n".join(f"{r['дата']} {'продавец' if r['направление'] == 'in' else 'мы'} ({r['канал']}): "
                                 f"{r['краткое содержание']}" for r in history),
            "deadlines": "\n".join(facts["deadlines"]) or "-",
            "unreadable": ", ".join(facts["unreadable"]) or "-",
            "documents": json.dumps([{k: v for k, v in d.items() if k != "link"} for d in docs if d["type"] != UNREAD],
                                    ensure_ascii=False)}


def render(st: dict, b: Brief, names: dict[str, str], update: bool) -> tuple[str, str]:
    """(subject, body). Laid out in code from the model's fields, so the format never drifts."""
    link = st["ссылка"] if st["ссылка"].startswith("http") and "mail.google" not in st["ссылка"] else ""
    def bullets(xs):
        return "\n".join(f"— {x.strip()}" for x in xs if x.strip())
    b.title = re.sub(rf"^\s*({re.escape(st['id'])}\s*[—–-]\s*)+", "", b.title).strip()  # the id is added below, once
    parts = [f"{st['id']} — {b.title}", f"Объявление: {link}" if link else "", b.overview.strip(),
             f"Подтверждено документами:\n{bullets(b.confirmed)}" if b.confirmed else "",
             f"Только со слов продавца:\n{bullets(b.seller_only)}" if b.seller_only else "",
             "Риски:\n" + bullets(f"{r.text.strip()} ({r.level})" for r in b.risks) if b.risks else "",
             f"Общее мнение. {b.opinion.strip()}",
             "До подписания:\n" + "\n".join(f"{i}. {s.strip()}" for i, s in enumerate(b.next_steps, 1)) if b.next_steps else "",
             "Вложения:\n" + "\n".join(names[f] for f in b.attach) if b.attach else ""]
    return (f"{'Обновление: ' if update else ''}{st['id']} — {b.title}: сводка для переговоров",
            "\n\n".join(p for p in parts if p))


def unhedge(text: str) -> str:
    """Last resort after the revision: sentences with a disclaimer or first-person hedging are dropped."""
    return "\n".join(" ".join(s for s in re.split(r"(?<=[.!?])\s+", line) if not HEDGE.search(s))
                     for line in text.split("\n"))


def compose(st: dict, docs: list[dict], history: list[dict], facts: dict, today: date) -> tuple[Brief, list[str]]:
    """Brief + fact-check + one revision. Returns the brief and what the second check still flags (shown to the owner)."""
    src = sources(st, docs, history, facts)
    def write(fix: str) -> Brief:
        return llm.call_json("model_review", COMPOSE.format(today=today.isoformat(), style=STYLE, fix=fix, **src), Brief,
                             effort="high", system=docs_.ANALYST, max_tokens=16000)
    def check(b: Brief) -> list[Check]:
        text = json.dumps(b.model_dump(), ensure_ascii=False)
        found = llm.call_json("model_review", VERIFY.format(today=today.isoformat(), brief=text,
                                                            **{k: v for k, v in src.items() if k != "unreadable"}),
                              Review, effort="high", system=docs_.ANALYST, max_tokens=8000).issues
        found += [Check(claim=m.group(0), problem="оговорка", fix="убрать") for m in HEDGE.finditer(text)]
        return found
    b = write("")
    issues = check(b)
    if issues:
        prev = json.dumps(b.model_dump(), ensure_ascii=False)
        fix = "\n".join(f"- {i.problem}: «{i.claim}» -> {i.fix}" for i in issues)
        b = write(f"\n<previous_draft>{prev}</previous_draft>\n<fact_check>Fix every one of these:\n{fix}</fact_check>")
        issues = check(b)
    return b, [f"{i.problem}: «{i.claim}» → {i.fix}" for i in issues if i.problem != "оговорка"]


class Request(pydantic.BaseModel):
    appropriate: bool
    reason_ru: str = pydantic.Field(description="one Russian sentence for the owner: why not, or which listed "
                                                "documents were left out and why; empty if all were asked")
    body: str = pydantic.Field(description="the letter with greeting, without signature; empty when not appropriate")


REQUEST = """Today is {today}. Write the next message to this seller or broker asking for the documents below, as a \
natural continuation of the conversation. Language: "{lang}". Channel: {channel}.
Leave out any listed document the seller said comes only after an NDA, a deposit, a signed contract, a call or a \
meeting (those are the owner's decisions), and any he promised to send fewer than {remind} days ago; name what you \
left out in reason_ru. A listed file that could not be read is asked for again as a readable copy. appropriate=false \
with an empty body when nothing is left to ask, when our last message already asked for these documents and is \
unanswered, or when the conversation is closed or hostile.
The letter: greet the person as earlier in the conversation; thank in one short sentence for the documents already \
sent, without listing them; ask for the documents as a short numbered list in the terms a Bulgarian uses, naming each \
by its kind and short number; say scans or phone photos are fine, sent {how}; one short closing line. No prices or \
amounts, no numbers of 9 or more digits, no offers, no deposit, NDA, meeting or call, no links, no parentheses, no \
signature, never mention other brokers or buyers.

<history>{history}</history>
<seller_last_message>{last}</seller_last_message>
<documents_received>{received}</documents_received>
<files_that_could_not_be_read>{unreadable}</files_that_could_not_be_read>
<previous_document_requests>{previous}</previous_document_requests>
<missing>{missing}</missing>"""
# «примечание» never goes into this prompt: it holds the owner's price bands and notes like «не говорить брокерам
# друг о друге», and the seller's own text sits right next to it (approval.INTERNAL)
CLOSING = ("[not_interested_or_stop]", "[sold]", "[suspicious]")  # the seller's last word ends any document request


def lang_of(st: dict, history: list[dict], mine: list[dict]) -> str:
    used = [x["язык"] for x in [*history, *mine] if x.get("язык") in ("bg", "en", "ru")]
    return used[-1] if used else "bg" if drafts.is_general(st) else drafts.language(st["заголовок"])


def threads_of(history: list[dict], mine: list[dict]) -> list[str]:
    """This station's own Gmail threads: a request never lands in another listing's conversation."""
    return list(dict.fromkeys(x["тред Gmail"] for x in [*history, *mine] if x.get("тред Gmail")))


def route(st: dict, msg: dict | None, lang: str, history: list[dict] = (), mine: list[dict] = ()) -> dict:
    """Where the request goes: where the seller last answered. A WhatsApp/Viber/alo.bg answer (logged by hand) gets
    it through «Ручная очередь» the way /note replies go. Otherwise a reply in this station's own thread (gmail.send
    threads it under the last message itself, so no Message-ID is stored that inbox would take as already
    answered), under our own letter when he never wrote by email, a new email only when there is no thread at all."""
    ins = [r for r in history if r["направление"] == "in"]
    if ins and ins[-1]["канал"] != "email" or st["канал"] != "email" and not msg:
        channel, to = inbox.manual_channel(st)
        return {"канал": channel, "кому": to, "тема": ""}
    clean = lambda subject: re.sub(r"^\s*((re|fw|fwd|отн)\s*:\s*)+", "", subject, flags=re.I)
    if msg:
        return {"канал": "email", "кому": parseaddr(gmail.header(msg, "From"))[1], "тред Gmail": msg["threadId"],
                "тема": f"Re: {clean(gmail.header(msg, 'Subject'))}"}
    ours = [d for d in mine if d.get("тред Gmail") and d["статус"] == "отправлено" and d["канал"] == "email"]
    if ours:
        return {"канал": "email", "кому": ours[-1]["кому"].split(",")[0].strip(), "тред Gmail": ours[-1]["тред Gmail"],
                "тема": f"Re: {clean(ours[-1]['тема'])}"}
    ref = drafts.description(st, lang)[1]
    return {"канал": "email", "кому": st["email"].split(",")[0].strip(),
            "тема": {"bg": f"Документи - {ref}", "en": f"Documents - {ref}", "ru": f"Документы - {ref}"}[lang]}


def request_block(st: dict, mine: list[dict], history: list[dict], last: dict | None, fp: str, cfg: dict,
                  today: date, paused: bool) -> str:
    """Why no document request may be drafted now ("" = it may). Code-side throttle; Claude still judges the context.
    «Диалоги» rows are in time order and carry only a date, so «who wrote last» is the last row, not the later date."""
    status = st["статус переписки"]
    if paused:
        return "отправка на паузе (/pause)"
    if status in CLOSED or status in inbox.PAUSED and status != inbox.OWNER_LED:
        return f"диалог ждёт решения ({status})"
    ins = [r for r in history if r["направление"] == "in"]
    if ins and ins[-1]["краткое содержание"].startswith(CLOSING):
        return "продавец отказался или написал подозрительное: решаешь ты"
    if any(d["статус"] in approval.WAITING or d["статус"] in ("одобрено", "отправляется") and not d.get("отправлено")
           for d in mine):
        return "у станции есть неотправленный черновик"
    last_in = ins[-1]["дата"] if ins else ""
    asked = [d for d in mine if d["тип"].startswith(DOC_REQUEST) and d.get("дата", "") >= last_in]
    if any(d["статус"] == "пропущено" for d in asked):
        return "ты пропустил прошлый запрос, жду ответа продавца"
    if sum(d["статус"] == "отправлено" for d in asked) >= 2:
        return "запрос и напоминание без ответа, дальше решаешь ты"
    if history and history[-1]["направление"] == "out" and _d(history[-1]["дата"]) \
            and (today - _d(history[-1]["дата"])).days < cfg["director"]["remind_days"]:
        return "ждём ответа на наше последнее сообщение"
    if last and last["запрос"].startswith("неуместно") and last["отпечаток"] == fp:
        return last["запрос"]  # judged for these very documents and messages: asked again only when they change
    return ""


def doc_request(st: dict, requests: list[str], docs: list[dict], history: list[dict], mine: list[dict], cfg: dict,
                today: date) -> tuple[dict | None, str]:
    """(draft without id, why not or what was left out). Claude writes it in the conversation's language, inside the
    station's own thread, and judges whether it fits."""
    lang = lang_of(st, history, mine)
    msg = None
    threads = threads_of(history, mine)
    if threads:
        try:
            msg = gmail.last_in_threads(gmail.service(), threads)
        except Exception as e:  # no readable thread: a new email or the manual channel
            print(f"brief: Gmail {st['id']}: {type(e).__name__}", flush=True)
    where = route(st, msg, lang, history, mine)
    email = where["канал"] == "email"
    previous = "\n".join(f"{d['дата']} {d['статус']}: {d['текст'][:600]}" for d in mine
                         if d["тип"].startswith(DOC_REQUEST)) or "-"
    user = REQUEST.format(today=today.isoformat(), lang=lang, remind=cfg["director"]["remind_days"],
                          channel="email" if email else f"{where['канал']} chat",
                          how="by email as attachments" if email else "in this same chat",
                          history="\n".join(f"{r['дата']} {'продавец' if r['направление'] == 'in' else 'мы'} "
                                            f"({r['канал']}): {r['краткое содержание']}" for r in history) or "-",
                          last=gmail.text(msg)[:6000] if msg else "-",
                          received="\n".join(sorted({d.get("title", d["file"]) for d in docs if d["type"] != UNREAD})) or "-",
                          unreadable="\n".join(d["file"] for d in docs if d["type"] == UNREAD) or "-",
                          previous=previous, missing="\n".join(f"- {r}" for r in requests))
    r = llm.call_json("model_strong", user, Request, effort="medium")
    if not r.appropriate or not r.body.strip():
        return None, r.reason_ru or "неуместно"
    sig = drafts.template(lang)["signature"]
    body = f"{r.body.strip()}\n\n{sig}"
    return {"id станции": st["id"], "дата": today.isoformat(), "язык": lang, "тип": DOC_REQUEST, **where, "текст": body,
            "статус": "на одобрении",
            "нарушения правил": ", ".join(rules.violations(f"{where['тема']}\n{body}", sig))}, r.reason_ru


def fingerprint(st: dict, docs: list[dict], history: list[dict], cfg: dict) -> str:
    """Changes when anything the verdict depends on changes: a file or its record, a seller message, the station's
    selection or operating answer, the criteria. «fp» keeps Sheets from reading a hex string as a number."""
    key = json.dumps([sorted(json.dumps(d, sort_keys=True, ensure_ascii=False) for d in docs),
                      sum(r["направление"] == "in" for r in history), st.get("отбор", ""), st.get("1 действующая", ""),
                      cfg["criteria"], cfg["director"]["min_days_left"]], ensure_ascii=False, default=str)
    return "fp" + hashlib.sha1(key.encode()).hexdigest()[:10]


def in_deal(st: dict) -> bool:
    """In a live conversation and passed the criteria (or an owner exception): its documents are read and briefed."""
    return st["статус переписки"] not in CLOSED and (st["отбор"] == "кандидат" or "исключение владельца" in st["отбор"])


def candidate(st: dict, files: list | None) -> bool:
    """In a deal, with documents in its Drive folder. Without files nothing is requested: documents may have come by
    a Drive link or WhatsApp (harvest and the bot bring them in)."""
    return in_deal(st) and bool(files)


def problem_lines(problems: list[tuple[str, str]]) -> str:
    return "\n".join(p + (f"{REQ}{r}" if r else "") for p, r in problems)


def parse_problems(text: str) -> list[tuple[str, str]]:
    return [(p, r) for p, _, r in (line.partition(REQ) for line in text.splitlines() if line.strip())]


ANALYSED = "не готово (анализ)"  # Claude found the blocker after the code gate passed: kept while nothing changes
ERROR = "ошибка"


def evaluate(st: dict, docs: list[dict], history: list[dict], mine: list[dict], last: dict | None, cfg: dict,
             today: date, paused: bool, force: bool) -> dict | None:
    """What changed for one station: {"row": a «Сводки» row, "brief"/"names"/"notes" when ready, "draft"/"why" for a
    document request, "seen": how many «Диалоги» rows it saw}. None when nothing changed."""
    fp = fingerprint(st, docs, history, cfg)
    fresh = force or not last or last["отпечаток"] != fp
    if not fresh and last["итог"] in ("готово", ERROR):
        return None
    problems, facts = gate(st, docs, cfg, today)
    analysed = False
    if not problems and not fresh and last["итог"] == ANALYSED:
        problems, analysed = parse_problems(last["проблемы"]), True
    elif not problems:
        b, notes = compose(st, docs, history, facts, today)
        price = b.price_eur
        if price is None:
            b.blocking.append(Issue(text="цена продавца неизвестна", request="цену, за которую продаётся объект"))
        elif facts["operating"] and facts["kwp"] and "исключение владельца" not in st["отбор"] \
                and price / facts["kwp"] > cfg["criteria"]["price_per_kwp_eur"]["skip_above"]:
            b.blocking.append(Issue(text=f"цена {price / facts['kwp']:.0f} €/kWp выше потолка "
                                         f"{cfg['criteria']['price_per_kwp_eur']['skip_above']}", request=""))
        if not b.blocking:
            names = {d["file_id"]: d["file"] for d in docs}
            b.attach = [f for f in dict.fromkeys(b.attach) if f in names]
            return {"row": {"id станции": st["id"], "дата": today.isoformat(), "отпечаток": fp, "итог": "готово",
                            "вложения": ",".join(b.attach), "статус": "на одобрении"},
                    "brief": b, "names": names, "notes": notes, "seen": len(history)}
        problems, analysed = [(i.text, i.request) for i in b.blocking], True
    lines = problem_lines(problems)
    requests = [r for _, r in problems if r]
    why = request_block(st, mine, history, last, fp, cfg, today, paused) if requests else "просить нечего"
    out = {"row": {"id станции": st["id"], "дата": today.isoformat(), "отпечаток": fp,
                   "итог": ANALYSED if analysed else "не готово", "проблемы": lines}, "why": why, "seen": len(history)}
    if not why:
        draft, out["why"] = doc_request(st, requests, docs, history, mine, cfg, today)
        out["draft"] = draft
        out["row"]["запрос"] = "" if draft else f"неуместно: {out['why']}"
    same = bool(last) and last["итог"] in ("не готово", ANALYSED) and last["проблемы"] == lines and not out.get("draft")
    if same and not fresh and not out["row"].get("запрос"):
        return None  # nothing new to tell the owner or to record
    # a seller message that changed nothing, or the same «неуместно» once more: recorded, not announced
    out["quiet"] = same and not force and out["row"].get("запрос", "") in ("", last["запрос"] if last else "")
    return out


HEADINGS = ("Подтверждено документами:", "Только со слов продавца:", "Риски:", "До подписания:", "Вложения:")


def brief_html(body: str) -> str:
    """The email text of a brief as a Telegram card: the title and the section headings bold, the listing a link."""
    out = []
    for n, line in enumerate(body.split("\n")):
        if n == 0 or line in HEADINGS:
            out.append(notify.b(line))
        elif line.startswith("Общее мнение. "):
            out.append(f"{notify.b('Общее мнение.')} {esc(line[len('Общее мнение. '):])}")
        elif line.startswith("Объявление: "):
            out.append(link(line[len("Объявление: "):], "Объявление"))
        else:
            out.append(esc(line))
    return "\n".join(out)


def send_card(st: dict, r: dict, row_no: int, b: Brief, notes: list[str], sizes: dict[str, int]) -> int:
    """The brief as the director will get it, then the attachments and the buttons. The buttons name the row and the
    station; approve() also checks the tapped message is this row's card."""
    notify.send(join(head("📋", "Сводка для директора", st["id"], "черновик"), brief_html(r["текст"])))
    mb = sum(sizes.get(f, 0) for f in b.attach) / 2 ** 20
    tail = join(head("📎", "Отправить директору?", st["id"]),
                f"{notify.b('Тема:')} {esc(r['тема'])}\n{notify.b('Вложений:')} {len(b.attach)} ({mb:.1f} МБ)",
                section("⚠️ Повторная проверка ещё видит", [esc(x) for x in notes]),
                "✅ уйдёт директору с этими документами, ⏭ — не отправлять.")
    return notify.send(tail, [[("✅ Директору", f"b:{row_no}:{st['id']}"), ("⏭ Не отправлять", f"bx:{row_no}:{st['id']}")]])


def harvest(stations: dict, dialogs: list[dict], drafts_rows: list[dict], files: dict, files_svc,
            seen: dict[str, set]) -> int:
    """Google Drive links a seller sent (in his letters in the station's own Gmail threads, in a logged WhatsApp
    message, in the station note) bring their files into the station folder, for every station in a deal.
    The system's own Drive links (saved attachments) are skipped. seen: station -> Drive ids already handled.
    A file that can't be taken (not shared, too big, a type never stored) is told to the owner once.
    Returns how many files were added."""
    ours = {f["id"] for fs in files.values() for f in fs}
    gsvc, total = None, 0
    for k, st in stations.items():
        if not in_deal(st):
            continue
        history = [r for r in dialogs if r["id станции"] == k]
        texts = [r["краткое содержание"] for r in history] + [st.get("примечание", "")]
        for tid in threads_of(history, [d for d in drafts_rows if d["id станции"] == k]):
            try:
                gsvc = gsvc or gmail.service()
                texts += gmail.thread_texts(gsvc, tid)
            except Exception as e:  # one unreadable thread never stops the pass
                print(f"brief: Gmail {k}: {type(e).__name__}", flush=True)
        links = [x for x in docs_.drive_links("\n".join(texts)) if x[1] not in ours]
        if not links:
            continue
        added, problems, later = docs_.import_drive(files_svc, k, links, files.setdefault(k, []), seen.setdefault(k, set()))
        for p in problems + later:
            print(f"brief: Drive {k}: {p}", flush=True)
        total += len(added)
        if added or problems:  # problems are told once (their ids are in seen now): an email when Telegram is down
            closed = any("открыт" in p or "закрыт" in p for p in problems)
            notify.urgent(join(head("📥", "Документы с Drive продавца", k, st["заголовок"][:60]),
                                 section(f"Добавлено в папку станции: {len(added)}", [esc(n) for n in added[:20]]
                                         + ([f"… и ещё {len(added) - 20}"] if len(added) > 20 else [])),
                                 section("Не загружено", [esc(p) for p in problems]),
                                 "Прочитаю и проверю в этом же проходе." if added else "",
                                 "Ссылка открывается только по приглашению: попроси продавца открыть доступ «всем, у "
                                 f"кого есть ссылка», потом перешли ссылку боту с подписью {code(k)}." if closed else ""))
    return total


def run(only: str = "", today: date | None = None) -> str:
    cfg, today = load_config(), today or date.today()
    api, sid = sheets.service(), os.environ["SPREADSHEET_ID"]
    sheets.ensure_tabs(api, sid)
    files_svc = drive.service()
    stations = {s["id"]: s for s in sheets.read(api, sid, "Станции")}
    dialogs, drafts_rows = sheets.read(api, sid, "Диалоги"), sheets.read(api, sid, "Черновики")
    files = drive.station_files(files_svc)
    # Drive files already handled, one «_state» key per station (a cell holds 50 000 characters): never fetched
    # again, even when not stored. The old single key is still honoured.
    state = sheets.kv(api, sid, "_state")
    load = lambda key: set(json.loads(state.get(key, {}).get("значение") or "[]"))
    legacy = load(SEEN)
    kept = {k: load(f"{SEEN} {k}") for k in stations}
    seen = {k: v | legacy for k, v in kept.items()}
    if harvest({k: v for k, v in stations.items() if not only or k == only.upper()}, dialogs, drafts_rows, files, files_svc,
               seen):
        files = drive.station_files(files_svc)
    if grown := {f"{SEEN} {k}": json.dumps(sorted(v - legacy)) for k, v in seen.items() if v - legacy != kept.get(k)}:
        sheets.set_kv(api, sid, "_state", grown)
    present = {k: {f["id"] for f in fs} for k, fs in files.items()}  # deleted or moved away: no longer counts
    live = {k for k, st in stations.items() if candidate(st, files.get(k))}
    cands = sorted(k for k in live if not only or k == only.upper())
    if only and not cands:
        return f"{only}: не кандидат для сводки (статус, отбор или нет документов)"
    try:
        docs_.refresh(api, sid, files, set(cands), files_svc, today)
    except llm.LLMUnavailable as e:  # what was read is saved; the rest is read next pass, the evaluation goes on
        print(f"brief: чтение документов: {e}", flush=True)
    reg = sheets.read(api, sid, "Документы")
    docs, registered = docs_.records(reg, present), {r["id файла"] for r in reg}
    briefs = sheets.read(api, sid, "Сводки")
    settings = {k: r["значение"] for k, r in sheets.kv(api, sid, "Настройки").items()}
    paused = settings.get("пауза") == "да"
    black, refused = approval.block_lists(api, sid)
    results = []
    for k in cands:
        st = stations[k]
        if inbox.blocked(st, black, refused) or any(f["id"] not in registered for f in files.get(k, [])):
            continue  # a refusal; or files refresh didn't get to (an outage): judged next pass, on all of them
        last = next((r for r in reversed(briefs) if r["id станции"] == k), None)
        history = [r for r in dialogs if r["id станции"] == k]
        try:
            recs = docs.get(k, [])
            if not gate(st, recs, cfg, today)[0] and not all(d.get("confirmed") for d in recs if d["type"] != UNREAD):
                docs_.confirm_station(api, sid, k, files_svc, today, present)  # the second reading decides
                recs = docs_.records(sheets.read(api, sid, "Документы"), present).get(k, [])
            res = evaluate(st, recs, history, [d for d in drafts_rows if d["id станции"] == k], last, cfg, today,
                           paused, force=bool(only))
        except llm.LLMUnavailable as e:  # an outage: this station waits for the next pass, the others go on
            print(f"brief: {k}: {e}", flush=True)
            continue
        except llm.LLMError as e:  # a refusal or a broken answer: recorded once, tried again when anything changes
            fp = fingerprint(st, docs.get(k, []), history, cfg)
            if last and last["итог"] == ERROR and last["отпечаток"] == fp:
                continue
            res = {"row": {"id станции": k, "дата": today.isoformat(), "отпечаток": fp, "итог": ERROR,
                           "проблемы": str(e)[:300]}, "error": str(e)[:300], "seen": len(history)}
        if res:
            results.append((k, res))
    # a brief waiting for ✅ whose station left the deal (sold, refused, files gone) never waits forever
    orphans = [r for r in briefs if r["статус"] == "на одобрении" and r["id станции"] not in live]
    if not results and not orphans:
        return "сводки: изменений нет"
    sizes = {f["id"]: int(f.get("size") or 0) for fs in files.values() for f in fs}
    return write(api, sid, results, orphans, sizes, settings, cfg, today)


def write(api, sid, results: list, orphans: list[dict], sizes: dict, settings: dict, cfg: dict, today: date) -> str:
    """Under the sheet lock, on fresh data: the bot's pass may have answered the seller or drafted something while
    Claude was working, so a document request is dropped when the station's dialog or drafts moved since; drafts get
    ids against the fresh «Черновики»; older open briefs become «устарело»; then the owner is told."""
    out = []
    with single_run("sheet", wait=600):
        stations = {s["id"]: s for s in sheets.read(api, sid, "Станции")}
        before = {k: dict(v) for k, v in stations.items()}
        by_id = {d["id"]: d for d in sheets.read(api, sid, "Черновики")}
        dialogs = sheets.read(api, sid, "Диалоги")
        rows = sheets.read(api, sid, "Сводки")
        auto = (settings.get("approval_mode") or cfg["approval_mode"]) == "auto"
        new_drafts, queue = [], []
        stale = [{**x, "статус": "устарело"} for x in rows if x["статус"] == "на одобрении"
                 and (x["id станции"] in {k for k, _ in results} or x["_row"] in {o["_row"] for o in orphans})]
        n = len(rows) + 2  # the next «Сводки» row: the buttons carry it
        for k, res in results:
            st, r = stations[k], res["row"]
            if "error" in res:
                notify.send(join(head("⚠️", "Документы не проверены", k), esc(res["error"]),
                                 "Повторю, когда по станции что-то изменится."))
                out.append(f"{k}: ошибка")
            elif "brief" in res:
                update = any(x["id станции"] == k and x["статус"] == "отправлено" for x in rows)
                r["тема"], r["текст"] = render(st, res["brief"], res["names"], update)
                r["текст"] = unhedge(r["текст"])
                r["tg сообщение"] = send_card(st, r, n, res["brief"], res["notes"], sizes)
                out.append(f"{k}: готово, сводка на ✅")
            else:
                d = res.get("draft")
                history = [x for x in dialogs if x["id станции"] == k]
                mine = [x for x in by_id.values() if x["id станции"] == k]
                moved = len(history) != res["seen"] or request_block(st, mine, history, None, "", cfg, today, False)
                if d and moved:  # the conversation moved while Claude was writing: asked again next pass if still needed
                    d, res["why"] = None, "переписка изменилась, пока готовился запрос: проверю в следующий раз"
                    r["запрос"] = ""
                owner_led = st["статус переписки"] == inbox.OWNER_LED  # the owner's conversation: always his ✅
                if d:
                    d["id"] = sheets.next_id([*by_id, *(x["id"] for x in new_drafts)])
                    r["запрос"] = d["id"]
                card = d and (owner_led or not auto or d["нарушения правил"])
                ask = (f"Документы не запрашиваю: {esc(res['why'])}." if not d else
                       f"📨 Запрос недостающего {code(d['id'])}: карточка ниже." if card else
                       f"📨 Запрос недостающего {code(d['id'])} одобрен автоматически.")
                if not res.get("quiet"):
                    notify.send(join(head("📋", "Директору пока рано", k, st["заголовок"][:60]),
                                     section("Чего не хватает", [esc(p) for p, _ in parse_problems(r["проблемы"])]), ask,
                                     f"{notify.b('Не вошло в запрос:')} {esc(res['why'])}" if d and res.get("why") else ""))
                if d:
                    approval.present(d, st, auto and not owner_led, queue)
                    new_drafts.append(d)
                out.append(f"{k}: не готово" + (f", запрос {d['id']}" if d else ""))
            r.setdefault("проблемы", "")
            n += 1
        approval.gate(api, sid, queue, stations)
        sheets.update(api, sid, "Сводки", stale)
        sheets.append(api, sid, "Сводки", [res["row"] for _, res in results])
        sheets.append(api, sid, "Черновики", new_drafts)
        sheets.append(api, sid, "Ручная очередь", queue)
        sheets.update(api, sid, "Станции", [s for k, s in stations.items() if s != before[k]])
    return "\n".join(out + [f"{o['id станции']}: сводка устарела" for o in orphans])


def tapped(api, sid, callback: str, message_id: int | None) -> tuple[dict | None, list[dict], str]:
    """The «Сводки» row a button belongs to: by its number, and only if the station and the card match."""
    rows = sheets.read(api, sid, "Сводки")
    num, _, station = callback.partition(":")
    r = next((x for x in rows if str(x["_row"]) == num), None)
    if not r or r["статус"] != "на одобрении" or station and r["id станции"] != station \
            or message_id and r["tg сообщение"] and str(r["tg сообщение"]) != str(message_id):
        return None, rows, "↩️ Эта сводка уже обработана, устарела или карточка не от неё."
    return r, rows, ""


def approve(api, sid, callback: str, today: date, message_id: int | None = None) -> str:
    """The owner's ✅: the station must still be a live deal and the gate must still pass on today's date; the row is
    claimed «отправляется» before Gmail, so a failure after the send never lets a second tap send it again."""
    cfg = load_config()
    r, _, why = tapped(api, sid, callback, message_id)
    if not r:
        return why
    st = next((s for s in sheets.read(api, sid, "Станции") if s["id"] == r["id станции"]), None)
    reg = sheets.read(api, sid, "Документы")
    svc = drive.service()
    present = {k: {f["id"] for f in fs} for k, fs in drive.station_files(svc).items()}  # deleted or moved away: gone
    docs = docs_.records(reg, present).get(r["id станции"], [])
    black, refused = approval.block_lists(api, sid)
    problems = ([("станции нет в таблице", "")] if not st else
                [(f"статус станции «{st['статус переписки']}»", "")] if st["статус переписки"] in CLOSED + inbox.HOLD else
                [(f"контакт {why}", "")] if (why := inbox.blocked(st, black, refused)) else gate(st, docs, cfg, today)[0])
    if problems:
        r["статус"] = "устарело"
        sheets.update(api, sid, "Сводки", [r])
        return join(head("⛔", "Сводку не отправил", r["id станции"]), section("Почему", [esc(p) for p, _ in problems]))
    files = {x["id файла"]: x["файл"] for x in reg if x["id файла"] in present.get(r["id станции"], ())}
    attach = [(files[f], drive.download(svc, f), drive.ALLOWED.get(Path(files[f]).suffix.lower(), "application/octet-stream"))
              for f in r["вложения"].split(",") if f in files]
    limit, kept, total = cfg["director"]["max_attach_mb"] * 2 ** 20, [], 0
    for a in attach:  # most important first: what doesn't fit stays with the owner, named in the letter
        if total + len(a[1]) <= limit:
            kept.append(a)
            total += len(a[1])
    left = [a[0] for a in attach if a not in kept]
    body = r["текст"] + (f"\n\nНе вложено из-за размера, есть у {cfg['owner']['name']['ru']}: {', '.join(left)}"
                         if left else "")
    to = cfg["director"]["email"]
    r["статус"] = "отправляется"
    sheets.update(api, sid, "Сводки", [r])
    try:
        res = gmail.send_files(gmail.service(), to, r["тема"], body, kept, DIRECTOR_LABEL)
    except gmail.NotSent as e:  # Gmail never got it: the card works again
        r["статус"] = "на одобрении"
        sheets.update(api, sid, "Сводки", [r])
        return join(head("⚠️", "Gmail не принял сводку", r["id станции"]), esc(e), "Нажми ✅ ещё раз позже.")
    if res.get("dry_run"):
        r["статус"] = "на одобрении"
        sheets.update(api, sid, "Сводки", [r])
        return head("🧪", "DRY_RUN: сводка директору не отправлена", r["id станции"])
    r.update({"статус": "отправлено", "отправлено": today.isoformat(), "тред Gmail": res["threadId"]})
    sheets.update(api, sid, "Сводки", [r])
    return join(head("📨", "Сводка отправлена директору", r["id станции"]), f"Кому: {esc(to)} · вложений: {len(kept)}"
                + (f"\nНе влезли по размеру: {esc(', '.join(left))}" if left else ""))


def skip(api, sid, callback: str, message_id: int | None = None) -> str:
    r, _, why = tapped(api, sid, callback, message_id)
    if not r:
        return why
    r["статус"] = "пропущено"
    sheets.update(api, sid, "Сводки", [r])
    return join(head("⏭", "Сводку не отправляю", r["id станции"]), "Новые документы по станции дадут новую.")


def status(api, sid, station_id: str) -> str:
    """/brief S0xx: the station's latest evaluation."""
    r = next((x for x in reversed(sheets.read(api, sid, "Сводки")) if x["id станции"] == station_id.upper()), None)
    if not r:
        return join(head("📋", "Сводка", station_id.upper()),
                    "Ещё не проверяли: в папке станции нет документов или она не кандидат.")
    if r["итог"] == "готово":
        return join(head("📋", "Сводка готова", r["id станции"], r["дата"]),
                    f"Статус: {notify.b(r['статус'])}" + (f", {esc(r['отправлено'])}" if r["отправлено"] else ""))
    if r["итог"] == ERROR:
        return join(head("⚠️", "Проверка не удалась", r["id станции"], r["дата"]), esc(r["проблемы"]))
    return join(head("📋", "Директору пока рано", r["id станции"], r["дата"]),
                section("Чего не хватает", [esc(p) + (f" → <i>запросить: {esc(q)}</i>" if q else "")
                                            for p, q in parse_problems(r["проблемы"])]),
                f"{notify.b('Запрос:')} {esc(r['запрос'])}" if r["запрос"] else "")


if __name__ == "__main__":
    from solar_outreach import director
    load_env()
    with single_run("brief", stale_sec=7200):  # the timer never runs two passes at once
        try:
            print(run(sys.argv[1] if len(sys.argv) > 1 else ""), flush=True)
        finally:  # what the director asked for goes out even when the pass above failed (it needs no LLM)
            if len(sys.argv) == 1:
                print(director.deliver(sheets.service(), os.environ["SPREADSHEET_ID"]), flush=True)

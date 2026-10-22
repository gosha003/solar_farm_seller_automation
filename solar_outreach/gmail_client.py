"""Gmail. Safety boundary:
- read and modify ONLY threads labelled SolarBG (checked in _thread, which every read goes through);
- the SolarBG label is added only to mail from addresses in «Станции» (adopt: decided by From in a Gmail search, the body is not read);
- no delete/trash: this module has neither;
- with DRY_RUN nothing goes to sellers; mail to the owner (alerts, end-to-end test) is allowed;
- internal letters (briefs to the director, send_files) carry their own label (SolarBG/директор), never SolarBG, so
  replies to them stay out of the seller pipeline; replies_from reads only threads under that label.
"""
import base64
import io
import os
import re
from functools import cache
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from bs4 import BeautifulSoup
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from solar_outreach.config import dry_run, load_config
from solar_outreach.google_auth import credentials

DONE_SUFFIX = "/обработано"


class NotSent(Exception):
    """Failed before Gmail got the letter (labels, token refresh): safe to retry, unlike an error during the send."""


@cache  # one client per process: build() on every daemon pass leaks memory (1.5 GB a day)
def service():
    return build("gmail", "v1", credentials=credentials(), cache_discovery=False).users()


def label_ids(svc, name: str) -> tuple[str, str]:
    """(SolarBG, SolarBG/обработано): created on first run."""
    have = {l["name"]: l["id"] for l in svc.labels().list(userId="me").execute(num_retries=3).get("labels", [])}
    for n in (name, name + DONE_SUFFIX):
        if n not in have:
            have[n] = svc.labels().create(userId="me", body={"name": n, "labelListVisibility": "labelShow",
                                                              "messageListVisibility": "show"}).execute()["id"]
    return have[name], have[name + DONE_SUFFIX]


def build_message(to: str, subject: str, body: str, cfg: dict, in_reply_to: str = "") -> EmailMessage:
    """Plain text, a normal From with a name, no bulk-mail headers (List-Unsubscribe, Precedence etc.)."""
    owner = cfg["owner"]["email"]
    m = EmailMessage()
    m["From"] = Address(cfg["gmail"]["sender_name"], *owner.split("@"))
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = make_msgid(domain=owner.split("@")[1])
    if in_reply_to:
        m["In-Reply-To"] = m["References"] = in_reply_to
    m.set_content(body)
    return m


def send(svc, to: str, subject: str, body: str, thread_id: str = "", in_reply_to: str = "") -> dict:
    """Send with the SolarBG label. DRY_RUN: nothing to sellers, returns {'dry_run': True}."""
    cfg = load_config()
    owner = cfg["owner"]["email"].lower()
    if dry_run() and parseaddr(to)[1].lower() != owner:
        return {"dry_run": True}
    try:
        label, _ = label_ids(svc, cfg["gmail"]["label"])
    except Exception as e:
        raise NotSent(f"{type(e).__name__}: {e}") from e
    if thread_id and not in_reply_to:  # a follow-up: Gmail and the seller's client thread it only with In-Reply-To
        try:
            t = _thread(svc, thread_id, label)
            in_reply_to = header(t["messages"][-1], "Message-ID") if t and t.get("messages") else ""
        except Exception:  # cosmetic: without it the follow-up still goes into the same Gmail thread
            pass
    raw = base64.urlsafe_b64encode(build_message(to, subject, body, cfg, in_reply_to).as_bytes()).decode()
    body_ = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
    try:
        sent = svc.messages().send(userId="me", body=body_).execute()
    except RefreshError as e:  # the token refresh happens before the request goes out
        raise NotSent(f"RefreshError: {e}") from e
    try:  # the letter is out: a failed label must not make the caller think it wasn't and send it again
        svc.messages().modify(userId="me", id=sent["id"], body={"addLabelIds": [label]}).execute()
    except Exception as e:  # adopt() labels the thread when the seller answers
        print(f"gmail: письмо {sent['id']} ушло, метка не поставлена ({type(e).__name__})", flush=True)
    return {"id": sent["id"], "threadId": sent["threadId"]}


def send_files(svc, to: str, subject: str, body: str, files: list[tuple[str, bytes, str]], label: str = "",
               thread_id: str = "", in_reply_to: str = "") -> dict:
    """An internal letter with attachments (name, bytes, mime type), e.g. a brief to the director or a reply in his
    thread, under its own label (never SolarBG: his replies must not enter the seller pipeline).
    DRY_RUN: only the owner's own address gets mail."""
    cfg = load_config()
    if dry_run() and parseaddr(to)[1].lower() != cfg["owner"]["email"].lower():
        return {"dry_run": True}
    try:
        own = label_ids(svc, label)[0] if label else ""
    except Exception as e:
        raise NotSent(f"{type(e).__name__}: {e}") from e
    m = build_message(to, subject, body, cfg, in_reply_to)
    for name, data, mime in files:
        main, _, sub = mime.partition("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    # media upload: a JSON body with raw base64 is capped at ~5 MB by the Gmail API
    media = MediaIoBaseUpload(io.BytesIO(m.as_bytes()), mimetype="message/rfc822", resumable=True)
    try:
        sent = svc.messages().send(userId="me", body={"threadId": thread_id} if thread_id else {}, media_body=media).execute()
    except RefreshError as e:
        raise NotSent(f"RefreshError: {e}") from e
    if own:
        try:  # the letter is out: a failed label only means its replies are not picked up
            svc.messages().modify(userId="me", id=sent["id"], body={"addLabelIds": [own]}).execute()
        except Exception as e:
            print(f"gmail: письмо {sent['id']} ушло, метка не поставлена ({type(e).__name__})", flush=True)
    return {"id": sent["id"], "threadId": sent["threadId"]}


def replies_from(svc, sender: str, label: str) -> list[dict]:
    """Unprocessed letters from `sender` in threads under an internal label (the director's answers to briefs).
    Only those threads are read; marked with mark_internal once handled."""
    lab, done = label_ids(svc, label)
    out = []
    # the query only narrows the listing (search writes a nested label with «-»); the check below decides
    q = f"from:({sender}) -label:" + re.sub(r"[\s/]", "-", label + DONE_SUFFIX)  # no backslash in f-strings on 3.11
    for t in svc.threads().list(userId="me", labelIds=[lab], q=q, maxResults=100).execute().get("threads", []):
        for m in svc.threads().get(userId="me", id=t["id"], format="full").execute().get("messages", []):
            if (parseaddr(header(m, "From"))[1].lower() == sender.lower() and done not in m.get("labelIds", [])
                    and "SENT" not in m.get("labelIds", [])):
                out.append(m)
    return out


def mark_internal(svc, msg: dict, label: str) -> None:
    """Handled, but left unread: the owner may want to read the director's letter himself."""
    lab, done = label_ids(svc, label)
    svc.messages().modify(userId="me", id=msg["id"], body={"addLabelIds": [lab, done]}).execute(num_retries=3)


def last_in_threads(svc, thread_ids: list[str]) -> dict | None:
    """The seller's newest letter in these threads (a station's own conversation), only one inbox has already handled
    (SolarBG/обработано): a document request never answers a letter that wasn't classified, escalated or checked."""
    cfg = load_config()
    label, done = label_ids(svc, cfg["gmail"]["label"])
    owner, best = cfg["owner"]["email"].lower(), None
    for tid in thread_ids:
        for m in (_thread(svc, tid, label) or {}).get("messages", []):
            ids = m.get("labelIds", [])
            newer = best is None or int(m.get("internalDate", 0)) > int(best.get("internalDate", 0))
            if parseaddr(header(m, "From"))[1].lower() != owner and "SENT" not in ids and done in ids and newer:
                best = m
    return best


def _thread(svc, thread_id: str, label: str) -> dict | None:
    """Read a thread only if it holds our letter labelled SolarBG. Otherwise None: other mail is not ours."""
    t = svc.threads().get(userId="me", id=thread_id, format="full").execute()
    return t if any(label in m.get("labelIds", []) for m in t.get("messages", [])) else None


# On public mail providers the whole address must match; on a corporate domain a colleague may answer (contact@ -> ivan@).
PUBLIC_MAIL = {"gmail.com", "googlemail.com", "abv.bg", "mail.bg", "dir.bg", "gbg.bg", "yahoo.com", "outlook.com",
               "hotmail.com", "live.com", "icloud.com", "mail.ru", "yandex.ru", "yandex.com", "proton.me", "protonmail.com"}
# the same providers under other country domains (gmx.de, yahoo.co.uk, hotmail.bg) and the big Russian/German ones
PUBLIC_NAMES = {"gmail", "googlemail", "yahoo", "ymail", "hotmail", "outlook", "live", "msn", "aol", "icloud", "me", "mac",
                "gmx", "web", "t-online", "freenet", "yandex", "ya", "mail", "bk", "list", "inbox", "rambler", "ukr", "proton",
                "protonmail", "pm", "zoho", "abv", "dir", "gbg", "seznam", "wp", "o2", "libero", "orange", "free", "laposte"}


def is_public(domain: str) -> bool:
    return domain in PUBLIC_MAIL or domain.split(".")[0] in PUBLIC_NAMES


def contact_query(emails: list[str], owner: str) -> str:
    """Senders for the Gmail search: the address on a public mail provider, otherwise the whole domain."""
    terms = set()
    for e in (x.strip().lower() for x in emails):
        if "@" in e and e != owner:
            domain = e.split("@")[1]
            terms.add(e if is_public(domain) else domain)
    return " OR ".join(sorted(terms))


def adopt(svc, emails: list[str]) -> int:
    """Seller mail without the SolarBG label (answered from another address on the domain, or the owner started the thread by hand):
    add the label so incoming() picks it up. Returns how many messages were taken."""
    cfg = load_config()
    who = contact_query(emails, cfg["owner"]["email"].lower())
    if not who:
        return 0
    label, _ = label_ids(svc, cfg["gmail"]["label"])
    q = f'from:({who}) -label:"{cfg["gmail"]["label"]}" -from:me newer_than:14d'
    ids, page = [], None
    while True:
        resp = svc.messages().list(userId="me", q=q, pageToken=page, maxResults=100).execute()
        ids += [m["id"] for m in resp.get("messages", [])]
        page = resp.get("nextPageToken")
        if not page:
            break
    for i in ids:
        svc.messages().modify(userId="me", id=i, body={"addLabelIds": [label]}).execute()
    return len(ids)


def incoming(svc) -> list[dict]:
    """Incoming mail in our threads not yet marked «обработано». Our own messages are skipped."""
    cfg = load_config()
    label, done = label_ids(svc, cfg["gmail"]["label"])
    owner = cfg["owner"]["email"].lower()
    out, page = [], None
    while True:
        resp = svc.threads().list(userId="me", labelIds=[label], pageToken=page, maxResults=100).execute()
        for t in resp.get("threads", []):
            thread = _thread(svc, t["id"], label)
            for m in (thread or {}).get("messages", []):
                frm = parseaddr(header(m, "From"))[1].lower()
                if frm != owner and done not in m.get("labelIds", []) and "SENT" not in m.get("labelIds", []):
                    out.append(m)
        page = resp.get("nextPageToken")
        if not page:
            return out


def mark_done(svc, msg: dict) -> None:
    """Mark processed: labels SolarBG + SolarBG/обработано. Only for mail from our threads (incoming returns those)."""
    label, done = label_ids(svc, load_config()["gmail"]["label"])
    svc.messages().modify(userId="me", id=msg["id"], body={"addLabelIds": [label, done], "removeLabelIds": ["UNREAD"]}).execute(
        num_retries=3)


def header(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower() == name.lower()), "")


def _parts(part: dict):
    yield part
    for p in part.get("parts", []) or []:
        yield from _parts(p)


def text(msg: dict) -> str:
    """Message text: text/plain, else text/html with tags stripped. Quoted earlier messages are cut off."""
    plain = html = ""
    for p in _parts(msg.get("payload", {})):
        data = p.get("body", {}).get("data")
        if not data:
            continue
        s = decode(base64.urlsafe_b64decode(data + "=="), p)
        if p.get("mimeType") == "text/plain" and not plain:
            plain = s
        elif p.get("mimeType") == "text/html" and not html:
            try:
                html = BeautifulSoup(s, "html.parser").get_text("\n")
            except Exception:  # markup html.parser rejects («<![ if]>»): tags stripped by hand, the message still gets read
                html = re.sub(r"<[^>]+>", " ", s)
    if plain:
        return strip_quote(plain)
    m = QUOTE.search(html)  # HTML quotes carry no «>» on our lines: cut at the quote, never keep «inline answers»
    return (html[:m.start()] if m else html).strip()


QUOTE = re.compile(r"^(>|On .{5,120} wrote:|На .{5,120} (написа|написал\w*):|-----Original Message|---+ ?Препратено|From: .+@"
                   r"|(От|От кого): .+@|-{3,}\s*(Оригинално съобщение|Исходное сообщение)"  # Bulgarian/Russian Outlook
                   r"|(От|From): .*\n(Изпратено|Отправлено|Sent|Дата|Date): )", re.M)


def strip_quote(s: str) -> str:
    """Our quoted letter is cut off. After a «>» quote or an «On ... wrote:» line, the seller's lines typed between our
    quoted questions are kept (inline answers); an Outlook-style quote below a header is cut as a whole."""
    m = QUOTE.search(s)
    if not m:
        return s.strip()
    head, rest = s[:m.start()], s[m.start():].splitlines()
    if not (rest[0].startswith(">") or re.search(r"wrote:|написа\w*:", rest[0])):
        return head.strip()
    keep = []
    for line in rest[1:]:
        if line.startswith(">") or not line.strip():
            continue
        if QUOTE.match(line):  # a nested Outlook header or a forward: the rest is not the seller's
            break
        keep.append(line)
    return "\n".join([head.strip(), *keep]).strip()


def attachment_parts(msg: dict) -> list[dict]:
    """Message parts with attachments (filename, mimeType, body.attachmentId). Nothing is opened or executed."""
    return [p for p in _parts(msg.get("payload", {})) if p.get("filename")]


def attachments(msg: dict) -> list[str]:
    return [p["filename"] for p in attachment_parts(msg)]


def attachment_bytes(svc, msg: dict, part: dict) -> bytes:
    """Attachment bytes. msg only ever comes from incoming(), i.e. from our SolarBG thread."""
    data = part.get("body", {}).get("data") or svc.messages().attachments().get(
        userId="me", messageId=msg["id"], id=part["body"]["attachmentId"]).execute()["data"]
    return base64.urlsafe_b64decode(data + "==")


def decode(raw: bytes, part: dict) -> str:
    """Part bytes in the part's own charset: Outlook in a Bulgarian/Russian locale still sends windows-1251 or KOI8-R."""
    ct = next((h["value"] for h in part.get("headers", []) if h["name"].lower() == "content-type"), "")
    m = re.search(r'charset="?([\w.:-]+)', ct, re.I)
    try:
        return raw.decode(m.group(1) if m else "utf-8", errors="replace")
    except (LookupError, UnicodeError):  # an unknown or unusable charset name («undefined», «idna»)
        return raw.decode("utf-8", errors="replace")


def is_delay(msg: dict) -> bool:
    """A «Delivery Status Notification (Delay)»: Gmail keeps retrying for up to 48 h, so it is not a bounce."""
    return is_bounce(msg) and bool(re.search(r"\(delay\)|delayed|still (being )?retr|will retry", header(msg, "Subject"), re.I))


def failed_recipient(msg: dict) -> str:
    return parseaddr(header(msg, "X-Failed-Recipients"))[1].lower()


def is_bounce(msg: dict) -> bool:
    frm = parseaddr(header(msg, "From"))[1].lower()
    return frm.startswith(("mailer-daemon@", "postmaster@")) or "delivery status notification" in header(msg, "Subject").lower()

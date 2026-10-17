"""Gmail. Safety boundary:
- read and modify ONLY threads labelled SolarBG (checked in _thread, which every read goes through);
- the SolarBG label is added only to mail from addresses in «Станции» (adopt: decided by From in a Gmail search, the body is not read);
- no delete/trash: this module has neither;
- with DRY_RUN nothing goes to sellers; mail to the owner (alerts, end-to-end test) is allowed.
"""
import base64
import os
import re
from functools import cache
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from bs4 import BeautifulSoup
from googleapiclient.discovery import build

from solar_outreach.config import dry_run, load_config
from solar_outreach.google_auth import credentials

DONE_SUFFIX = "/обработано"


@cache  # one client per process: build() on every daemon pass leaks memory (1.5 GB a day)
def service():
    return build("gmail", "v1", credentials=credentials(), cache_discovery=False).users()


def label_ids(svc, name: str) -> tuple[str, str]:
    """(SolarBG, SolarBG/обработано): created on first run."""
    have = {l["name"]: l["id"] for l in svc.labels().list(userId="me").execute().get("labels", [])}
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
    label, _ = label_ids(svc, cfg["gmail"]["label"])
    raw = base64.urlsafe_b64encode(build_message(to, subject, body, cfg, in_reply_to).as_bytes()).decode()
    body_ = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
    sent = svc.messages().send(userId="me", body=body_).execute()
    svc.messages().modify(userId="me", id=sent["id"], body={"addLabelIds": [label]}).execute()
    return {"id": sent["id"], "threadId": sent["threadId"]}


def _thread(svc, thread_id: str, label: str) -> dict | None:
    """Read a thread only if it holds our letter labelled SolarBG. Otherwise None: other mail is not ours."""
    t = svc.threads().get(userId="me", id=thread_id, format="full").execute()
    return t if any(label in m.get("labelIds", []) for m in t.get("messages", [])) else None


# On public mail providers the whole address must match; on a corporate domain a colleague may answer (contact@ -> ivan@).
PUBLIC_MAIL = {"gmail.com", "googlemail.com", "abv.bg", "mail.bg", "dir.bg", "gbg.bg", "yahoo.com", "outlook.com",
               "hotmail.com", "live.com", "icloud.com", "mail.ru", "yandex.ru", "yandex.com", "proton.me", "protonmail.com"}


def contact_query(emails: list[str], owner: str) -> str:
    """Senders for the Gmail search: the address on a public mail provider, otherwise the whole domain."""
    terms = set()
    for e in (x.strip().lower() for x in emails):
        if "@" in e and e != owner:
            domain = e.split("@")[1]
            terms.add(e if domain in PUBLIC_MAIL else domain)
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
    svc.messages().modify(userId="me", id=msg["id"], body={"addLabelIds": [label, done], "removeLabelIds": ["UNREAD"]}).execute()


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
        s = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        if p.get("mimeType") == "text/plain" and not plain:
            plain = s
        elif p.get("mimeType") == "text/html" and not html:
            html = BeautifulSoup(s, "html.parser").get_text("\n")
    return strip_quote(plain or html)


QUOTE = re.compile(r"^(>|On .{5,120} wrote:|На .{5,120} (написа|написал\w*):|-----Original Message|---+ ?Препратено|From: .+@)", re.M)


def strip_quote(s: str) -> str:
    m = QUOTE.search(s)
    return (s[:m.start()] if m else s).strip()


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


def is_bounce(msg: dict) -> bool:
    frm = parseaddr(header(msg, "From"))[1].lower()
    return frm.startswith(("mailer-daemon@", "postmaster@")) or "delivery status notification" in header(msg, "Subject").lower()

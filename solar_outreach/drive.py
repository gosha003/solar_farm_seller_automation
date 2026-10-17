"""Seller attachments -> Google Drive: one folder per station inside «Solar BG вложения».
Only PDF, images and DOCX are saved; nothing is opened or executed. Other types: name only, in «Диалоги».
scope drive.file: the system sees only the Drive files it created itself.

ponytail: no text is extracted from attachments; add a safe parser when documents must be read
automatically rather than just stored for the owner.
"""
from functools import cache
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from solar_outreach import gmail_client as gmail
from solar_outreach.google_auth import credentials

ROOT = "Solar BG вложения"
FOLDER = "application/vnd.google-apps.folder"
ALLOWED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic", ".webp": "image/webp"}


@cache  # one client per process: build() on every daemon pass leaks memory (1.5 GB a day)
def service():
    return build("drive", "v3", credentials=credentials(), cache_discovery=False).files()


def folder(files, name: str, parent: str = "") -> str:
    q = f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and mimeType = '{FOLDER}' and trashed = false"
    q += f" and '{parent}' in parents" if parent else ""
    found = files.list(q=q, fields="files(id)", spaces="drive").execute().get("files", [])
    if found:
        return found[0]["id"]
    body = {"name": name, "mimeType": FOLDER, **({"parents": [parent]} if parent else {})}
    return files.create(body=body, fields="id").execute()["id"]


def save(files, gsvc, msg: dict, station_id: str) -> list[str]:
    """Save a letter's attachments to the station folder. Returns lines for «Диалоги» and alerts."""
    out, dest = [], ""
    for part in gmail.attachment_parts(msg):
        name = part["filename"]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED:  # by file extension, not the mimeType in the email: the sender controls that
            out.append(f"{name} (не сохранено: тип {ext or '?'})")
            continue
        dest = dest or folder(files, station_id, folder(files, ROOT))
        media = MediaInMemoryUpload(gmail.attachment_bytes(gsvc, msg, part), mimetype=ALLOWED[ext])
        f = files.create(body={"name": name, "parents": [dest]}, media_body=media, fields="webViewLink").execute()
        out.append(f"{name}: {f['webViewLink']}")
    return out

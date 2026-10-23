"""Seller attachments -> Google Drive: one folder per station inside «Solar BG вложения».
Only PDF, images and DOCX are saved; nothing is opened or executed. Other types: name only, in «Диалоги».
Documents from other channels (WhatsApp, a seller's Drive link) are added from the PC with `documents add`.
scope drive.file: the system sees only the Drive files it created itself. documents.py reads them (sent to the
Claude API as data, never executed).
"""
import io
from functools import cache
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

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
    found = files.list(q=q, fields="files(id)", spaces="drive").execute(num_retries=3).get("files", [])
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


def station_files(files) -> dict[str, list[dict]]:
    """Station id -> its files (id, name, size, webViewLink), oldest first. Two listings: the station folders, then
    every file the app created (drive.file), grouped by folder."""
    root = folder(files, ROOT)
    def listing(q: str, fields: str) -> list[dict]:
        out, page = [], None
        while True:
            r = files.list(q=q, fields=f"nextPageToken,files({fields})", pageToken=page, pageSize=1000,
                           spaces="drive", orderBy="createdTime").execute()
            out += r.get("files", [])
            page = r.get("nextPageToken")
            if not page:
                return out
    folders = {f["id"]: f["name"] for f in listing(f"'{root}' in parents and mimeType = '{FOLDER}' and trashed = false",
                                                   "id,name")}
    out: dict[str, list[dict]] = {}
    for f in listing(f"mimeType != '{FOLDER}' and trashed = false",
                     "id,name,size,webViewLink,parents,md5Checksum,appProperties"):
        station = next((folders[p] for p in f.get("parents", []) if p in folders), None)
        if station:
            out.setdefault(station, []).append(f)
    return out


def download(files, file_id: str) -> bytes:
    buf = io.BytesIO()
    loader = MediaIoBaseDownload(buf, files.get_media(fileId=file_id))
    done = False
    while not done:
        _, done = loader.next_chunk()
    return buf.getvalue()


def upload(files, path: Path, station_id: str) -> str:
    """A document from another channel into the station folder. Same type allowlist as attachments."""
    return put(files, path.name, path.read_bytes(), station_id)


def put(files, name: str, data: bytes, station_id: str, source: str = "") -> str:
    """Bytes into the station folder. source (a seller's Drive file id, a Telegram file id) is kept in appProperties,
    so the same file is never fetched or stored twice."""
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"{name}: тип {ext or '?'} не принимается (только {', '.join(ALLOWED)})")
    dest = folder(files, station_id, folder(files, ROOT))
    body = {"name": name, "parents": [dest], **({"appProperties": {"source": source}} if source else {})}
    media = MediaInMemoryUpload(data, mimetype=ALLOWED[ext])
    return files.create(body=body, media_body=media, fields="id").execute(num_retries=3)["id"]


def tag(files, file_id: str, source: str) -> None:
    """An existing copy of a source file (same bytes, uploaded by hand): remember the source on it."""
    files.update(fileId=file_id, body={"appProperties": {"source": source}}).execute(num_retries=3)

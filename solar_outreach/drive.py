"""Вложения продавцов -> Google Drive (разделы 4, 5): папка на станцию внутри «Solar BG вложения».
Сохраняем только PDF, изображения и DOCX, ничего не открываем и не запускаем. Остальные типы: только имя в «Диалогах».
scope drive.file: система видит в Drive только то, что создала сама.

ponytail: текст из вложений не извлекается (раздел 4 разрешает безопасный парсер); добавить, когда понадобится
автоматически читать документы, а не только хранить их для владельца.
"""
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from solar_outreach import gmail_client as gmail
from solar_outreach.google_auth import credentials

ROOT = "Solar BG вложения"
FOLDER = "application/vnd.google-apps.folder"
ALLOWED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".heic": "image/heic", ".webp": "image/webp"}


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
    """Сохранить вложения письма в папку станции. Возвращает строки для «Диалогов» и уведомлений."""
    out, dest = [], ""
    for part in gmail.attachment_parts(msg):
        name = part["filename"]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED:  # по расширению, а не по mimeType из письма: его задаёт отправитель
            out.append(f"{name} (не сохранено: тип {ext or '?'})")
            continue
        dest = dest or folder(files, station_id, folder(files, ROOT))
        media = MediaInMemoryUpload(gmail.attachment_bytes(gsvc, msg, part), mimetype=ALLOWED[ext])
        f = files.create(body={"name": name, "parents": [dest]}, media_body=media, fields="webViewLink").execute()
        out.append(f"{name}: {f['webViewLink']}")
    return out

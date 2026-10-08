"""Канал связи по приоритету раздела 10: email (авто) > телефон (ручная очередь) > сайт (ручная очередь)."""


def channel(emails: list[str], phones: list[str]) -> str:
    if emails:
        return "email"
    if phones:
        return "телефон (ручная)"
    return "сайт (ручная)"

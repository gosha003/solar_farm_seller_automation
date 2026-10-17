"""Contact channel by priority: email (automatic) > phone (manual queue) > website (manual queue)."""


def channel(emails: list[str], phones: list[str]) -> str:
    if emails:
        return "email"
    if phones:
        return "телефон (ручная)"
    return "сайт (ручная)"

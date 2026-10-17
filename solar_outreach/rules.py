"""Hard rules: every outgoing text is checked. An empty list = OK to show/send.

The check is deliberately crude (regex, no LLM): an extra escalation to the owner beats a letter containing an offer.
"""
import re

CHECKS = [
    ("слово про бота/систему/ИИ", re.compile(
        r"\b(систем\w*|бот\w*|асистент\w*|ассистент\w*|ИИ|изкуствен\w* интелект|искусственн\w* интеллект\w*"
        r"|AI|A\.I\.|bot|chatbot|assistant|system|automated|автоматич\w*|автоматичн\w*)\b", re.I)),
    ("сумма/цена покупки/бюджет", re.compile(
        r"\d[\d\s.,]*\s*(€|eur\w*|евро|лв\.?|лева|bgn|usd|\$|долар\w*|доллар\w*|хил\.|хиляди|тыс\w*|k\b|млн\.?|million)"
        r"|(€|\$)\s*\d|бюджет\w*|budget|\d+\s*(€|eur|евро)\s*/\s*(kw|квт)|на (kw|квт)\b", re.I)),
    ("офер/предложение цены", re.compile(
        r"\bоферт\w*|\boffer\w*|\bоффер\w*|предлага\w* (Ви |вам |you )?(цена|сума)|бихме платили|we (would|will|can) pay"
        r"|готови сме да платим|готовы заплатить|ще (я |го )?купим|we will buy|мы (её |ее )?купим|гарантира\w*"
        r"|guarantee", re.I)),  # «търсим да закупим» (we want to buy: intent) is fine, «ще купим» (we will buy: a promise) is not
    ("встреча/звонок/осмотр/NDA/задаток", re.compile(
        r"\bсреща\w*|\bmeeting|\bвстреч\w*|оглед\w*|осмотр\w*|\bviewing|\bvisit\b|посещени\w*|\bNDA\b"
        r"|споразумение за поверителност|предварителен договор|preliminary (contract|agreement)|задат\w*|депозит\w*"
        r"|\bdeposit|аванс\w*|ще (Ви )?се обадя|позвъня|call you|позвоню|созвон\w*", re.I)),
    ("реквизиты/ЕИК/банк", re.compile(r"\bЕИК\b|\bБУЛСТАТ\b|\bIBAN\b|\bSWIFT\b|банкова сметка|bank account|ИНН|реквизит\w*", re.I)),
    ("ссылка", re.compile(r"https?://|www\.", re.I)),
]
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def violations(text: str, signature: str) -> list[str]:
    out = [name for name, rx in CHECKS if rx.search(text)]
    allowed = {digits(p) for p in PHONE.findall(signature)}
    phones = [d for d in map(digits, PHONE.findall(text)) if len(d) >= 9]  # dates like "08.10.2026" are not phones
    if any(d not in allowed for d in phones):
        out.append("чужой номер телефона")  # sellers only ever get the owner's number from the signature
    if signature.strip() not in text:
        out.append("нет подписи")
    return out

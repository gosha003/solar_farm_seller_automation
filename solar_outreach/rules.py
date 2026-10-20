"""Hard rules: every outgoing text is checked. An empty list = OK to show/send.

The check is deliberately crude (regex, no LLM): an extra escalation to the owner beats a letter containing an offer.
"""
import re

CHECKS = [
    ("слово про бота/систему/ИИ", re.compile(  # бот(?!ев): the places Ботевград/Ботево are not bots
        r"\b(систем\w*|бот(?!ев)\w*|асистент\w*|ассистент\w*|ИИ|изкуствен\w* интелект|искусственн\w* интеллект\w*"
        r"|AI|A\.I\.|bot|chatbot|assistant|system|automated|автоматич\w*|автоматичн\w*)\b", re.I)),
    ("человек/за границей", re.compile(  # never claim to be human, never say where the buyer is
        r"\b(човек\w*|човешк\w*|человек\w*|humans?|real person|робот\w*|robots?)\b|чужбин\w*|чуждестран\w*"
        r"|иностран\w*|\bforeign\w*|зад? границ\w*|за рубеж\w*|abroad|overseas", re.I)),
    ("сумма/цена покупки/бюджет", re.compile(
        r"\d[\d\s.,]*\s*(€|eur\w*|евро|лв\.?|лева|bgn|usd|\$|долар\w*|доллар\w*|хил\.|хиляд\w*|тыс\w*|k\b|млн\.?|million"
        r"|милион\w*|миллион\w*|mln|mio)"
        r"|(€|\$|\beur\b|\bbgn\b|\busd\b|\bлв\.?|\bевро)\s*\d|бюджет\w*|budget|\d+\s*(€|eur|евро)\s*/\s*(kw|квт)"
        r"|на (kw|квт)\b|\b(милион\w*|миллион\w*|million\w*)\b", re.I)),  # «около милион евро»: no digit needed
    ("офер/предложение цены", re.compile(
        r"\bоферт\w*|\boffer\w*|\bоффер\w*|предлага\w* (Ви |вам |you )?(цена|сума)|бихме платили"
        r"|we (would|will|can|could) pay|(можем|бихме могли|готови сме) да платим|ще (Ви )?платим"
        r"|(можем|готовы|могли бы) (за)?платить|заплатим|\bbid\b"
        r"|ще (я |го )?купим|we will buy|мы (её |ее )?купим|гарантира\w*"
        r"|guarantee", re.I)),  # «търсим да закупим» (we want to buy: intent) is fine, «ще купим» (we will buy: a promise) is not
    ("встреча/звонок/осмотр/NDA/задаток", re.compile(  # (?<!с ): «с оглед на» means «in view of»
        r"\bсреща\w*|\bmeeting|\bвстреч\w*|(?<!с )оглед\b|оглед\w+|осмотр\w*|\bviewing|\bvisit\w*|\binspect\w*|посещени\w*"
        r"|\bNDA\b|споразумение за поверителност|non-disclosure|confidentiality agreement|конфиденциальн\w*"
        r"|предварителен договор|предварительн\w* договор\w*|preliminary (contract|agreement)|letter of intent|\bLOI\b"
        r"|писмо за намерение|задат[ъо]?к\w*|капаро\w*|депозит\w*|\bdeposit|аванс\w*"
        r"|дойд\w* да (видим|разгледа\w*)|come (and|to) see|разгледа\w* (централата|на място)"
        r"|обад\w*|обаждане|позвън\w*|\bcall\b|позвон\w*|звонок|созвон\w*", re.I)),
    ("реквизиты/ЕИК/банк", re.compile(r"\bЕИК\b|\bБУЛСТАТ\b|\bIBAN\b|\bSWIFT\b|банкова сметка|банковск\w* сч[её]т"
                                      r"|bank account|ИНН|реквизит\w*", re.I)),
    ("ссылка", re.compile(r"https?://|www\.|(?<![@\w.-])[\w-]+\.(bg|com|net|org|eu|ru|me|info)\b", re.I)),
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

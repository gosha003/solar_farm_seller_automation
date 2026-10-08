# Solar BG outreach

Ищет объявления о продаже действующих ФЕЦ 1-2 МВт в Болгарии, пишет продавцам от имени Ивана Петрова (Пример Солар ЕООД), собирает ответы в Google Sheets и сообщает владельцу только то, что требует его решения. Полное ТЗ в [SPEC.md](SPEC.md).

**По умолчанию `DRY_RUN=true`.** Письма реально уходят только при явном `DRY_RUN=false` в окружении, и не раньше Фазы 3.

## Фазы

| Фаза | Что | Статус |
|---|---|---|
| 0 | Подготовка: каркас, ключи, OAuth, выбор запуска | в работе |
| 1 | Поиск, парсинг, дедуп, скоринг, Google Sheet | |
| 2 | Черновики bg/en/ru, одобрение в Telegram (DRY_RUN) | |
| 3 | Gmail: отправка под одобрение, чтение ответов, эскалации | |
| 4 | Автоотправка, дожимы, дайджест, bounce-стоп | |
| 5 | Развёртывание, мониторинг, `/pause`, ротация ключей | |

## Решения Фазы 0 (отклонения от ТЗ)

- Запуск: GitHub Actions, приватный репо. Основной цикл `*/30` (не 15-20 мин: каждый запуск округляется до целой минуты, `*/20` = 2160 мин/мес > 2000 бесплатных), поиск отдельным workflow 4 раза в сутки.
- Лимит: 30 новых первых писем в сутки, без разгона до 100.
- Классификация и извлечение фактов: `claude-haiku-5-5` (в ТЗ `claude-haiku-4-5`, он в 10 раз дороже).

## Установка (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
Copy-Item .env.example .env   # затем заполнить
```

## Секреты

Только в `.env` локально и в GitHub Secrets (`gh secret set -f .env`). `.env`, `client_secret*.json`, `token*.json` в `.gitignore`. В чат и в код ключи не вставлять.

## Фаза 0: что сделать владельцу

### 1. Claude API
1. https://console.anthropic.com → **Settings → Limits**: поставить месячный лимит расходов ($20 хватит с запасом).
2. **API Keys → Create Key** → вставить в `.env` как `ANTHROPIC_API_KEY=...`.

### 2. Telegram-бот
1. В Telegram написать **@BotFather** → `/newbot` → имя и username бота → получить токен → `.env`: `TELEGRAM_BOT_TOKEN=...`.
2. Написать своему боту `/start`.
3. Открыть в браузере `https://api.telegram.org/bot<ТОКЕН>/getUpdates`, найти `"chat":{"id":123456789` → `.env`: `TELEGRAM_CHAT_ID=123456789`.

### 3. Google Cloud (Gmail, Sheets, Drive)
1. https://console.cloud.google.com → выбор проекта → **New project** → `solar-bg`.
2. **APIs & Services → Library**: включить **Gmail API**, **Google Sheets API**, **Google Drive API**.
3. **Google Auth Platform → Get started**: App name `Solar BG`, support email `owner@example.com`, Audience **External**, contact email тот же → Create.
4. **Audience → Publish app → In production.** Важно: в режиме Testing refresh token умирает через 7 дней. Проходить проверку Google не нужно, приложение личное: при входе будет экран «Google hasn't verified this app» → **Advanced → Go to Solar BG (unsafe)**.
5. **Clients → Create client → Desktop app** → Create → **Download JSON** → сохранить как `client_secret.json` в корень проекта.
6. Запустить и войти как `owner@example.com`, разрешить все три доступа:
   ```powershell
   .venv\Scripts\python -m solar_outreach.google_auth
   ```
   Скрипт проверит, что вошли именно в этот ящик, и запишет `GOOGLE_*` в `.env`.

Доступы: `gmail.modify` (без права безвозвратного удаления), `spreadsheets`, `drive.file` (только файлы, созданные самой системой). Ограничение «трогать только треды с меткой `SolarBG`» обеспечивает код, не Google: scope `gmail.modify` технически даёт доступ ко всему ящику.

## Структура

```
solar_outreach/
  config.yaml      # критерии, лимиты, режимы
  config.py        # загрузка config.yaml и .env, флаг DRY_RUN
  google_auth.py   # одноразовая OAuth-авторизация
tests/
```
Остальные модули из раздела 5 ТЗ появляются в своих фазах.

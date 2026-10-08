# Solar BG outreach

Ищет объявления о продаже действующих ФЕЦ 1-2 МВт в Болгарии, пишет продавцам от имени Ивана Петрова (Пример Солар ЕООД), собирает ответы в Google Sheets и сообщает владельцу только то, что требует его решения. Полное ТЗ в [SPEC.md](SPEC.md).

**По умолчанию `DRY_RUN=true`.** Письма реально уходят только при явном `DRY_RUN=false` в окружении, и не раньше Фазы 3.

## Фазы

| Фаза | Что | Статус |
|---|---|---|
| 0 | Подготовка: каркас, ключи, OAuth, выбор запуска | готово |
| 1 | Поиск, парсинг, дедуп, скоринг, Google Sheet | ждёт alo.bg (429) |
| 2 | Черновики bg/en/ru, одобрение в Telegram (DRY_RUN) | в работе: 10 черновиков у владельца |
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

### 1-2. Claude API и Telegram-бот
1. https://console.anthropic.com → **Settings → Limits**: месячный лимит расходов ($20 хватит с запасом) → **API Keys → Create Key**, скопировать.
2. В Telegram: **@BotFather** → `/newbot` → имя и username бота (должен кончаться на `bot`) → скопировать токен.
3. Запустить и вставить оба ключа (ввод скрыт, ключи проверяются, chat id определяется сам):
   ```powershell
   .venv\Scripts\python -m solar_outreach.set_secrets
   ```
   Тот же скрипт для ротации ключей: пустой Enter пропускает шаг.

### 3. Google Cloud (Gmail, Sheets, Drive)
1. https://console.cloud.google.com → выбор проекта → **New project** → `solar-bg`.
2. **APIs & Services → Library**: включить **Gmail API**, **Google Sheets API**, **Google Drive API**.
3. **Google Auth Platform → Get started**: App name `Solar BG`, support email `owner@example.com`, Audience **External**, contact email тот же → Create.
4. **Branding**: home page `https://gosha003.github.io/solar-bg-pages/`, privacy `https://gosha003.github.io/solar-bg-pages/privacy.html`, authorized domain `gosha003.github.io` (страницы в публичном репо [solar-bg-pages](https://github.com/gosha003/solar-bg-pages)). Затем **Audience → Publish app → In production.** В режиме Testing refresh token умирает через 7 дней. Проверка Google не нужна, приложение личное: на экране «Google hasn't verified this app» → **Advanced → Go to Solar BG (unsafe)**.
5. **Clients → Create client → Desktop app** → Create → **Download JSON** → сохранить как `client_secret.json` в корень проекта.
6. Запустить и войти как `owner@example.com`, разрешить все три доступа:
   ```powershell
   .venv\Scripts\python -m solar_outreach.google_auth
   ```
   Скрипт проверит, что вошли именно в этот ящик, и запишет `GOOGLE_*` в `.env`.

Доступы: `gmail.modify` (без права безвозвратного удаления), `spreadsheets`, `drive.file` (только файлы, созданные самой системой). Ограничение «трогать только треды с меткой `SolarBG`» обеспечивает код, не Google: scope `gmail.modify` технически даёт доступ ко всему ящику.

## Поиск (Фаза 1)

```powershell
.venv\Scripts\python -m solar_outreach.search           # пишет в Google Sheet (создаёт при первом запуске, ID в .env)
.venv\Scripts\python -m solar_outreach.search --local   # только печать
```

Площадки и почему так: см. docstring [sources/__init__.py](solar_outreach/sources/__init__.py). robots.txt проверен вручную, блокировки и капчи не обходятся, скрытые за кнопкой телефоны не раскрываются.

## Черновики (Фаза 2)

```powershell
.venv\Scripts\python -m solar_outreach.approval            # нажатия/правки из Telegram + черновики новых станций
.venv\Scripts\python -m solar_outreach.approval --test 9   # плюс тестовые черновики по мелким станциям
```

Письмо = шаблон из `templates/{bg,en,ru}.yaml` + факты из таблицы (мощность, место), без LLM. «Править» в Telegram: Claude Sonnet переписывает по инструкции, `rules.py` проверяет правила раздела 4, нарушения показываются владельцу. Продавцам в Фазе 2 ничего не уходит: одобренные email-письма ждут Фазы 3, одобренные телефон/сайт попадают во вкладку «Ручная очередь» с готовыми ссылками WhatsApp/Viber.

## Структура

```
solar_outreach/
  config.yaml      # критерии, лимиты, режимы
  config.py        # загрузка config.yaml и .env, флаг DRY_RUN
  google_auth.py   # одноразовая OAuth-авторизация
  set_secrets.py   # ввод ключей Anthropic/Telegram
  sources/         # bazar.bg, alo.bg, kpd.bg + список недоступных площадок
  parse.py         # мощность, цена, год, режим, контакты
  qualify.py       # отбор и скоринг по config.yaml
  contacts.py      # канал связи
  sheets.py        # Google Sheets: вкладки, чтение/запись, цвета статусов
  search.py        # поиск -> дедуп -> вкладка «Станции»
  templates/       # письма bg/en/ru (раздел 7)
  drafts.py        # язык, частник/брокер, сборка писем, ссылки WhatsApp/Viber
  rules.py         # проверка правил раздела 4 для любого исходящего текста
  llm.py           # Claude: строгий JSON (pydantic), повтор, кэш системного промпта
  notify.py        # Telegram: сообщения с кнопками, getUpdates только от владельца
  approval.py      # цикл одобрения черновиков
tests/
```

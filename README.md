# Solar BG outreach

Ищет объявления о продаже действующих ФЕЦ 1-2 МВт в Болгарии, пишет продавцам от имени Ивана Петрова (Пример Солар ЕООД), собирает ответы в Google Sheets и сообщает владельцу только то, что требует его решения. Полное ТЗ в [SPEC.md](SPEC.md).

**С 2026-10-13 по решению владельца система работает без одобрения:** `DRY_RUN=false`, режим `auto` (по умолчанию в config.yaml и во вкладке «Настройки»). Письма без нарушений правил раздела 4 одобряются и уходят сами, включая ответы продавцам (`alo_reply note`). Человеку остаются эскалации (цена, встреча, «кто вы», NDA, деньги, подозрительное письмо) и каналы без автоматизации: WhatsApp/Viber/телефон и сайты кроме alo.bg лежат в «Ручной очереди». Сообщения на alo.bg из очереди отправляет задача на ПК владельца через Chrome (`python -m solar_outreach.site_queue`). `/auto off` и `/pause` возвращают ручной режим.

Запуск цикла: `.venv\Scripts\python -m solar_outreach.engine` (входящие Gmail, нажатия Telegram, черновики, отправка одобренного).

## Фазы

| Фаза | Что | Статус |
|---|---|---|
| 0 | Подготовка: каркас, ключи, OAuth, выбор запуска | готово |
| 1 | Поиск, парсинг, дедуп, скоринг, Google Sheet | готово, кроме alo.bg (429, перепроверка) |
| 2 | Черновики bg/en/ru, одобрение в Telegram | готово (10/10 одобрено) |
| 3 | Gmail: отправка под одобрение, чтение ответов, эскалации | готово, тест на ящике владельца пройден; DRY_RUN=false, режим manual |
| 4 | Автоотправка, дожимы, дайджест, bounce-стоп | готово (сутки без вмешательства 2026-10-09); режим manual, DRY_RUN=true по решению владельца |
| 5 | Развёртывание, мониторинг, `/pause`, ротация ключей | готово 2026-10-10: VM, пульс, потолок $5, расходы 0.00 CAD |

## Решения Фазы 0 (отклонения от ТЗ)

- Запуск (пересмотрено в Фазе 5): бесплатная VM Google Cloud e2-micro вместо GitHub Actions. Бот отвечает за секунды (long polling), почта проверяется раз в минуту, нет лимита минут Actions.
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

## Автоматизация (Фаза 4)

- Команды в Telegram: `/stats`, `/pause`, `/resume`, `/auto on|off`, `/queue`, `/digest`, `/blacklist <контакт>`, `/status <id>`. Режим и пауза хранятся во вкладке «Настройки» (`approval_mode`, `пауза`).
- `auto`: черновик без нарушений правил раздела 4 одобряется сам; эскалации, нарушения и тестовые черновики всегда идут владельцу. Bounce за сутки >10% возвращает режим manual и шлёт срочное уведомление.
- Лимиты: 30 первых писем в сутки; за один запуск не больше 1 первого письма или дожима (`outreach_per_run`), поэтому интервал между запусками и есть пауза между письмами. Ответы в диалогах без лимита.
- Дожимы: через 4 и 9 дней после первого email, в том же треде, если продавец не ответил (автоответ не считается). Пропущенный дожим останавливает дожимы этой станции.
- Дайджест: раз в сутки после 8:00 по Торонто, Telegram (запасной канал: письмо `[SOLAR]`).
- Вложения: PDF, изображения и DOCX сохраняются в Drive, `Solar BG вложения/<id станции>`; ссылки попадают в «Диалоги» и в эскалации. Остальные типы: только имя.
- Сбои: срочное уведомление после 3 сбоев подряд, последняя ошибка попадает в дайджест.

## Развёртывание (Фаза 5)

VM `solar-bot`, Google Cloud проект `solar-bg`, зона `us-east1-b`, всё в бесплатном лимите:
e2-micro, стандартный диск 30 ГБ (не balanced: он платный), без снапшотов, без RDP.
Потолок расходов: бюджет 7 CAD (≈ $5, валюта аккаунта CAD) в месяц. Бесплатный тариф (FREE_TIER) вычитается, кредиты триала (PROMOTION) нет: бюджет видит то, что пришлось бы платить деньгами. Сумма за месяц пишется в `_state` → «расходы Google Cloud» и в дайджест. Письма Google на 20% (≈ $1), 50%, 90%, 100%.
На 100% отчёт приходит в Pub/Sub `billing-alerts`, демон (раз в 5 мин, `budget.py`) шлёт срочное уведомление и выключает VM.
Сам бюджет Google ничего не ограничивает, ограничивает только это выключение; отчёты Google запаздывают до суток.
Сервис-аккаунт VM `solar-bot` может только читать подписку `billing-alerts-vm`.
Трафик к Gmail/Drive бесплатный, остальное (Telegram, Claude, сайты) далеко ниже 1 ГБ/мес.

- `solar-bot` (systemd): `engine --daemon`. Почта раз в минуту, между проверками ждёт Telegram (ответ за секунды). Падение: перезапуск через 30 с; 3 сбоя подряд: срочное уведомление.
- `solar-search-sites.timer` 3 раза в сутки, `solar-search-web.timer` раз в сутки (с веб-поиском Claude), `solar-search-regions.timer` по средам (веб-поиск отдельно по областям Монтана, Видин, Добрич, Шумен, Ямбол, Хасково, Кърджали), `solar-search-brokers.timer` по понедельникам (поиск брокеров: строки «брокер:сайт» во вкладке «Станции», письмо без ссылки на объявление, email уходит на одобрение, без email контакты в «Ручной очереди»). Поиск держит блокировку таблицы только на время записи.
- Код и `.env` с компьютера владельца: `deploy/deploy.sh` (только код: `deploy/deploy.sh --code`). На VM ключи лежат в `~/solar/.env` (chmod 600), в репозиторий и GitHub не попадают.
- Необязательно: `HEALTHCHECK_URL` в `.env` (healthchecks.io), пинг раз в 5 мин; если VM или демон лежат, healthchecks пришлёт письмо.

### Пауза и остановка
- `/pause` в Telegram: продавцам ничего не уходит, входящие разбираются, эскалации приходят. `/resume` продолжает.
- Полный стоп бота: `gcloud compute ssh solar-bot --zone us-east1-b --command "sudo systemctl stop solar-bot"` (запуск: `start`).
- Стоп всего, включая VM: `gcloud compute instances stop solar-bot --zone us-east1-b`.
- Состояние и логи: `gcloud compute ssh solar-bot --zone us-east1-b --command "sh solar/deploy/status.sh"`.

### Ротация ключей
1. Claude / Telegram: `.venv\Scripts\python -m solar_outreach.set_secrets` (старый ключ отозвать в консоли Anthropic / `/revoke` у BotFather).
2. Google: `.venv\Scripts\python -m solar_outreach.google_auth` (старый доступ отозвать на myaccount.google.com/permissions).
3. `sh deploy/deploy.sh`: новый `.env` уйдёт на VM, бот перезапустится.

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
  approval.py      # кнопки Telegram, черновики, лимиты, отправка, дайджест
  owner.py         # команды владельца, /stats, текст дайджеста
  followup.py      # дожимы 4/9 дней
  inbox.py         # входящие: классификация, факты, эскалации
  gmail_client.py  # Gmail только в тредах SolarBG
  drive.py         # вложения в Drive
  engine.py        # один запуск цикла (раздел 9)
tests/
```

# IngVertuVpn

Единый приватный монорепозиторий проекта: production-код бота, веб-компоненты, эксплуатационные документы и архив доступного фрагмента старой Notion-инструкции. Снимок подготовлен 2026-11-15.

## Структура

В корне размещены `bot.py`, `rw_sync.py`, `admin_web.py`, `admin_web.html`, `me_cabinet.py`, `game.py`, каталог `game/`, `site_stats.py` и `incy_page.html`. Папка `docs/` содержит архитектуру, актуальный инвентарь, runbook-и, безопасность, миграцию Notion и архив доступного старого текста.

## Production

Бот работает с `130.12.46.8`: `/opt/zpnbot`, venv `/opt/zpnbot/venv`, systemd `ingvertuvpn-bot.service`, пользователь `ingvertu`. Runtime секреты загружаются из `/etc/ingvertuvpn/bot.env`; SQLite path берётся из `INGVERTU_DB` (на момент снимка `/opt/zpnbot/ingvertuvpn.sqlite3`). Панель находится в `/opt/remnawave`. Домены: `panel.vertuwpn.ru` и `sub.vertuwpn.ru`.

Две подключённые ноды на момент последней проверки: `91.205.220.23` и `195.96.132.187`, порт data plane `1234`. Третий сервер `194.5.65.28` не включён: доступ по предоставленному паролю не прошёл проверку. Новые подписки используют Remnawave `/api/sub/<short-id>` и в текущем профиле отдают `ss://` конфигурации. Старые `/conf/<uuid>` ссылки перенаправляются на активный endpoint.

Оплата в клоне отключена/заглушена; Platega ещё не интегрирована. Не включать реальные списания без отдельной реализации и проверки.

## Быстрый контроль

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m py_compile bot.py rw_sync.py admin_web.py game.py me_cabinet.py site_stats.py
```

Поля окружения перечислены в `.env.example`. Он содержит только пустые placeholders. Не запускайте второй polling-процесс с тем же Telegram token.

## Конфиденциальность и полнота

Репозиторий должен оставаться **Private**. Здесь намеренно нет токенов, паролей, пользовательских UUID, subscription URLs, SSH-ключей, SQLite базы, production env, резервных копий или сертификатов. Секреты хранятся отдельно на серверах; после передачи в чат их нужно отозвать/заменить. Подробности — в [`docs/security.md`](docs/security.md).

Полная миграция исходных Notion-страниц пока не завершена: одна копия инструкции обрывается на `Loading…`, а страницы доступа и Notion Chat/Invite требуют входа. Содержимое, которое удалось получить, заархивировано, а пробелы отмечены в [`docs/notion-migration.md`](docs/notion-migration.md). Для завершения нужен Markdown/HTML/PDF экспорт либо публичные ссылки на все страницы.

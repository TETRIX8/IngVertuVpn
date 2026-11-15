# Актуальный инвентарь

Снимок 2026-11-15. IP-адреса и пути приведены для частной эксплуатации; репозиторий должен оставаться Private.

| Роль | Адрес / hostname | Размещение | Статус |
|---|---|---|---|
| Bot + Panel host | `130.12.46.8` | Ubuntu 24; bot `/opt/zpnbot`; Python venv `/opt/zpnbot/venv`; env `/etc/ingvertuvpn/bot.env`; SQLite `/opt/zpnbot/ingvertuvpn.sqlite3` | Bot active; Panel stack healthy |
| Panel UI | `panel.vertuwpn.ru` | Remnawave в `/opt/remnawave` | HTTPS; вход через собственную учётную запись Panel |
| Subscription | `sub.vertuwpn.ru` | Public reverse proxy / Remnawave subscription route | HTTPS; `/api/sub/<short-id>` проверен |
| Node 1 | `91.205.220.23` | Remnawave Node; пользовательский Shadowsocks inbound на `1234/tcp` | Последняя проверка: reachable, включён в subscription |
| Node 2 | `195.96.132.187` | Remnawave Node; пользовательский Shadowsocks inbound на `1234/tcp` | Последняя проверка: reachable, включён в subscription |
| Node 3 | `194.5.65.28` | Планировался как дополнительный Node | Не установлен: SSH-аутентификация отклонена; не добавлен в подписку |
| Legacy source | `149.33.48.28` (Germany) | Источник исходного кода: `/opt/zpnbot` | Исторический source host; его старые секреты/данные не копировать автоматически |

## Production services / directories

- `ingvertuvpn-bot.service`; `User=ingvertu`; `WorkingDirectory=/opt/zpnbot`; command `/opt/zpnbot/venv/bin/python /opt/zpnbot/bot.py`.
- Environment file: `/etc/ingvertuvpn/bot.env` (содержит секреты; не открывать/копировать в Git). Переменная `INGVERTU_DB` задаёт SQLite path.
- Remnawave Compose stack: `/opt/remnawave`; containers `remnawave`, `remnawave-db`, `remnawave-redis`, `remnawave-local-proxy`.
- Reverse proxy host mounts: `/opt/remnawave/nginx/nginx.conf`, `/opt/remnawave/nginx/certs`, `/opt/remnawave/nginx/htpasswd`.
- Public domains: `panel.vertuwpn.ru`, `sub.vertuwpn.ru`, DNS zone `vertuwpn.ru`.

## Текущая конфигурация бота

Имена runtime variables перечислены в `IngVertuVpn/.env.example`; фактические значения находятся только в защищённом серверном env. На момент снимка `INGVERTU_ACCESS_ISSUANCE_ENABLED=1`; endpoint подписки `/api/sub`; публичная схема payload — Shadowsocks (`ss://`). Платежи в клоне выключены, Platega не подключена.

## Устаревшая Notion-информация

Старый ZPN ONE snapshot называет `/opt/zpnbot/zpn.db`, `zpnbot`, `zpn-rw-sync` и `@ZPN_ONEbot`. Это относится к предыдущей версии и не должно подменять актуальные пути/имена таблицы выше. Подробности — в архивном извлечении.

# Runbook: релиз и восстановление

## Перед релизом

1. Проверить diff, `python -m py_compile` и secret scan.
2. Сделать отдельную копию production кода и SQLite backup с согласованным/безопасным способом (для SQLite использовать online backup API или остановку/краткую quiesce, а не просто копировать открытый файл).
3. Подтвердить, что backup доступен и не находится в Git.
4. Сверить `.env`-схему; никогда не заменять production env файлом из репозитория.
5. Для релиза сначала развернуть в staging, затем атомарно заменить файлы и только после этого перезапустить systemd unit.

## Production paths

- Working tree: `/opt/zpnbot`.
- Python: `/opt/zpnbot/venv/bin/python`.
- Unit: `ingvertuvpn-bot.service`, user `ingvertu`.
- Secret environment: `/etc/ingvertuvpn/bot.env` (не коммитить/не копировать в docs).
- Bot DB: path from `INGVERTU_DB`; текущий path `/opt/zpnbot/ingvertuvpn.sqlite3`.
- Panel stack: `/opt/remnawave`.

## Проверка после релиза

- `systemctl is-active ingvertuvpn-bot.service`.
- `journalctl -u ingvertuvpn-bot.service --since '10 minutes ago'` — сначала очистить/замаскировать пользовательские ID, UUID, token-like strings.
- Проверить `docker ps` и Remnawave health без показа environment values.
- Пройти synthetic subscription test, убедиться, что test account удалён.
- Не включать новый узел или выдачу ключей, пока Node health и public payload проверены.

## Восстановление

Остановить сервис, восстановить последние согласованные source и DB backup, проверить ownership/modes, запустить unit и выполнить read-only smoke tests. SQLite DB и Node state являются отдельными данными и не восстанавливаются из Git. Если DB утеряна, не импортировать случайные данные из старого Notion/источника без сверки с пользователем.

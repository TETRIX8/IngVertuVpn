# Безопасность и секреты

## Никогда не хранить в Git

- Telegram bot token и admin credentials;
- Remnawave API tokens, node `SECRET_KEY`, Panel account password, PostgreSQL/Redis secrets;
- Cloudflare API token;
- SSH passwords/private keys;
- populated env files, SQLite базы, backups, журналы с пользовательскими данными;
- полные subscription URLs, UUID пользователей, QR images или экспортированные client configs.

Файл `/etc/ingvertuvpn/bot.env` — production источник переменных; ограничить его чтение root и сервисным механизмом systemd. Создать в репозитории только `.env.example` с пустыми полями. Репозиторий Private не является заменой секрет-менеджеру.

## Ротация

Значения bot token, root passwords и Cloudflare token уже отправлялись в чат во время настройки. Считать их раскрытыми: отозвать/перевыпустить bot token через BotFather, заменить Cloudflare token и сменить root/Panel/API credentials. Порядок ротации: создать новый секрет → обновить защищённое окружение → проверить сервис и DNS/API → отозвать старый → убедиться, что журнал не содержит значений.

При ротации bot token обновлять только `/etc/ingvertuvpn/bot.env`, после чего выполнить `systemctl restart ingvertuvpn-bot.service` и проверить статус/journal. Не коммитить снимок env или вывод команд.

## Git правила

- Репозитории приватные.
- Перед коммитом проверить `git status --short`, `.gitignore`, историю и secret scanner.
- Не помещать приглашения Notion, access URLs, SSH key files, DB backups или exports с credentials.
- Добавлять секреты только в серверный secret store/защищённый env; не в docs.
- Перед передачей/публикацией делать code review и проверку лицензий сторонних библиотек.

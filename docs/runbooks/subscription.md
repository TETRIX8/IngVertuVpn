# Runbook: диагностика подписки

## Фактический текущий формат

Публичный Remnawave URL: `https://sub.vertuwpn.ru/api/sub/<16 hex chars>`. Тело — base64 список `ss://` ссылок; тест 2026-11-15 вернул две линии с узлами `91.205.220.23` и `195.96.132.187`. Это не VLESS-ссылка и не HTML-страница. Импортировать URL как subscription в совместимом клиенте.

## Исправленный дефект

Ранее helper в bot выдавал `https://sub.vertuwpn.ru/conf/<uuid>`, но `/conf` route отсутствовал, и такой URL отвечал 404. Исправлено в `bot.py`: новые кнопки формируют `/api/sub/<short-id>`, а старые `/conf/<uuid>` дают redirect к Remnawave endpoint. Проверка действительной активной учётки после deploy: HTTP 200, две `ss://` линии, оба существующих endpoint-а.

## Последовательность диагностики

1. Убедиться, что пользователь активен в bot SQLite и `needs_sync=0`.
2. Проверить пользователя по `shortUuid` и `status=ACTIVE` в Remnawave; UUID и subscription URL не помещать в логи/тикеты.
3. Сделать HTTP GET subscription endpoint с корректным client User-Agent. Записать только status, content-type, byte count, число распознанных lines, схемы и адреса узлов.
4. Если HTTP не 200: проверить DNS/TLS, reverse-proxy access/error log без запроса body с секретом, API user/squad и Remnawave service health.
5. Если HTTP 200, но клиент сообщает timeout при обновлении: проверить, может ли телефон открыть `https://sub.vertuwpn.ru` по текущей сети, правильность полной ссылки и client support for Shadowsocks.
6. Если subscription обновляется, но сервер не соединяется: отдельная проверка TCP `:1234`, Node/Xray status, inbound/Host mapping, firewall и клиентский handshake.
7. Не тестировать с действительной пользовательской ссылкой в публичных checker-сайтах. После отправки ссылки в общий чат следует сменить ключ.

## Безопасный серверный smoke test

Use temporary test credentials/user; always delete in `finally`. Never print the generated full config or user UUID. Verify only response status, line count, expected scheme, expected endpoint presence and clean removal.

# BOR — Python Telegram-бот проекта «Ребилд»

Первая версия: Python 3.12 + aiogram 3.31. Команды `/start`, `/help`, `/version`, `/book demo`, адресные ссылки на книги, проверка Python и Docker в GitHub Actions.

**Важно:** исходники сами по себе не означают, что бот запущен в Telegram. Для живого запуска нужны отдельный токен BotFather и постоянный компьютер/сервер.

## Быстрый запуск

1. Создать тестового бота через официальный [@BotFather](https://t.me/BotFather).
2. Выполнить `cp .env.example .env`, записать секретный `BOT_TOKEN` в локальный `.env`.
3. Запустить `docker compose --env-file .env -f compose.local.yml up -d --build`.
4. Проверить `/start`, `/version`, `/book demo` и `https://t.me/<bot_username>?start=book_demo`.

`catalog.json` содержит только демонстрационную карточку, а не настоящую книгу.

## Автоматическое обновление

GitHub Actions проверяет Python и Docker на каждом изменении. После однократной настройки сервера и секретов выпуск из `main` автоматически разворачивается при включённой переменной `BOR_DEPLOY_ENABLED=true`. Подробности: [docs/deployment.md](docs/deployment.md).

Бот использует long polling, поэтому внешний веб-сервер и входящий HTTPS ему не нужны. Для одного BotFather-токена нужен один работающий экземпляр. Не наделять бот правами модератора до разработки и проверки модерации.

Связанные требования: [Бот Ребилд в Notion](https://app.notion.com/p/3ff32a84beec81b6940ec94961d43338). Этот Python-репозиторий не является копией другого Rust-репозитория.

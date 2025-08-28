# RU Bookmakers Arbitrage Scanner — v0.7 (Stable, Proxy, Stake Split, EXE build)

Функционал:
- БК: **Fonbet + Winline** (проверено) + **Olimpbet/Betcity** (экспериментальные, через URL в .env)
- Прокси: HTTP/HTTPS/SOCKS, ротация, отдельный прокси для Telegram
- Ретраи + бэкофф + кэширование + лимиты запросов + интервалы по хостам
- Антиспам алертов
- Расчёт **ставок** из банка `BANK_SIZE` (фиксированный профит на любом исходе)
- Docker и REST (`/healthz`, `/arbs`)

## Быстрый старт (Docker)
см. предыдущие версии — команды идентичны.

## Запуск без Docker (Windows .exe)
Вы можете получить .exe двумя способами:

### A) GitHub Actions (рекомендуется — без установки Python)
1. Создайте приватный репозиторий на GitHub и загрузите файлы проекта.
2. Положите `.env` **в корень** (или создайте позже рядом с .exe).
3. В репо уже есть workflow `.github/workflows/build-windows.yml`. После пуша он соберёт артефакт.
4. Зайдите в раздел **Actions** → последний workflow → **Artifacts** → скачайте `arb-scanner-windows-exe.zip`.

### B) Локально через PowerShell (потребуется Python один раз)
1. Установите Python 3.11 x64 и `pip install -r requirements.txt pyinstaller==6.6.0`.
2. Выполните:
```
pyinstaller --onefile --name arb-scanner --add-data "arbitrage_scanner;arbitrage_scanner" run_server.py
```
3. Готовый `arb-scanner.exe` будет в `dist/`. Положите рядом `.env` и запускайте.

> Примечание: вариант A не требует установки Python у вас на машине — сборку делает GitHub.

## Как пользоваться .exe
- Скопируйте `.env` (см. `.env.example`) рядом с `arb-scanner.exe` и заполните.
- Запустите `arb-scanner.exe`; сервис поднимется на `http://127.0.0.1:8000`.
- Проверка: `http://127.0.0.1:8000/healthz` и `http://127.0.0.1:8000/arbs`.

## Настройка BANK_SIZE и стейков
`BANK_SIZE=1000` — банк в валюте аккаунта. Бот в алерте пришлёт объём ставок для каждого исхода, рассчитанный так, чтобы зафиксировать одинаковый профит вне зависимости от результата.

## Дисклеймер
Эндпоинты неофициальные, меняются. Используйте на свой риск и соблюдайте правила БК.

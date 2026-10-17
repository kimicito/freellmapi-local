# freellmapi-local

Локальная установка [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) как единого OpenAI-совместимого LLM-шлюза для hy-memory (pro-режим) и фоновых задач. Без ежемесячной платы: провайдеры - бесплатные тиры.

## Архитектура

```
hy-memory / скрипты
    -> http://127.0.0.1:3001/v1  (freellmapi, unified key)
        -> http://127.0.0.1:8300/v1  (ghmodels_shim) -> GitHub Models (free tier)
        -> https://openrouter.ai/api/v1 (custom provider) -> free-модели (:free)
```

- `freellmapi.service` - systemd-юнит роутера, установлен в `/opt/freellmapi`, порт 3001, только 127.0.0.1.
- `ghmodels-shim.service` + `ghmodels_shim.py` - нормализатор: GitHub Models иногда отдаёт `text/plain` вместо chat.completion JSON; shim оборачивает в корректный OpenAI-ответ (или пропускает JSON как есть).
- `hy-memory.env.example` - переменные для pro-режима hy-memory (реальный файл: `hymemory-data/.env`, chmod 600).

## Порядок установки (с нуля)

1. `git clone --depth 1 https://github.com/tashfeenahmed/freellmapi /opt/freellmapi`
2. `cd /opt/freellmapi/server && npm ci && npm run build`
3. `cp .env.example .env`: `HOST=127.0.0.1`, `PORT=3001`, сгенерировать `ENCRYPTION_KEY` (`node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`)
4. Установить юниты: `cp freellmapi.service ghmodels-shim.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now freellmapi ghmodels-shim`
5. Первый аккаунт: `curl -X POST http://127.0.0.1:3001/api/auth/setup -H 'Content-Type: application/json' -d '{"email":"...","password":"..."}'` (с loopback код не нужен; токен сохранить)
6. Unified key в БД: `sqlite3 ...` или страница Keys дашборда; сохранить в `secrets/freellmapi-unified-key.txt`
7. Добавить провайдеров через `POST /api/keys/custom` (см. итоговую конфигурацию ниже)

## Провайдеры (наша конфигурация)

| keyId | Провайдер | Модели | Статус |
|---|---|---|---|
| 2 | GitHub Models через shim (127.0.0.1:8300) | openai/gpt-4o-mini, openai/gpt-4o | деградировал: endpoint отдаёт stub «OK» в text/plain (17.10); shim держит канал живым, но ответы бесполезны - можно отключить |
| 3 | OpenRouter free pool | google/gemma-4-26b-a4b-it:free, google/gemma-4-31b-it:free, xingchen-agi/xing4.0-29b-a4b:free | рабочий; лимиты OpenRouter free (~50 req/день на модель, бывают 429 - retry) |

Ключи: GitHub PAT (`secrets/github-pat.txt`), OpenRouter (`sk-or-...` из `process_excel.py`). Шифруются freellmapi (AES-256-GCM).

## Проверка

```bash
KEY=$(cat /root/.openclaw/secrets/freellmapi-unified-key.txt)
curl -s http://127.0.0.1:3001/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"google/gemma-4-26b-a4b-it:free","messages":[{"role":"user","content":"Say OK"}],"max_tokens":10}'
```

Дашборд: `http://127.0.0.1:3001` (логин паролем из шага 5). Логи: `journalctl -u freellmapi -f`.

## Зачем

- hy-memory pro: LLM-конденсация чанков в L2-факты (`MEMORY_LLM_*` в `hy-memory.env.example`)
- Фоновые cron-задачи без расхода лимитов основной сессии
- Единая точка для смены провайдера без правки скриптов

## Туннель на VPS pricewize (добавлено 18.10)

Pricewise на timeweb (200.165.227.84) получает LLM через reverse SSH-туннель на этом хосте:
- `freellmapi-tunnel.service` (этот хост): `-R 127.0.0.1:8301:127.0.0.1:3001 root@200.165.227.84`, Restart=always, ключ `secrets/freellmapi-tunnel` (ed25519, добавлен в authorized_keys VPS)
- На VPS LLM endpoint: `http://127.0.0.1:8301/v1` (только loopback, GatewayPorts=no)
- ⚠️ Порт 3001 на VPS занят чужим сервисом (/opt/safemind), поэтому 8301
- Проверено end-to-end 18.10: VPS → 8301 → freellmapi → OpenRouter free → ответ

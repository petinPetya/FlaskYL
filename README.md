# ДЛЯ ЗАПУСКА app.py С АДМИНСКИМИ ФУНКЦИЯМИ НЕОБХОДИМ ФАЙЛ .flask-env, НО ОН НЕ БУДЕТ ДОБАВЛЕН В РЕПОЗИТОРИЙ, ТАК КАК СОДЕРЖИТ ЧУВСТВИТЕЛЬНЫЕ ДАННЫЕ.

- `SECRET_KEY`
- `DATABASE_URL`
- `LOG_LEVEL`
- `SESSION_COOKIE_SECURE`
- `REMEMBER_COOKIE_SECURE`
- `PREFERRED_URL_SCHEME`
- `BOOTSTRAP_SCHEMA_ON_STARTUP`

Если `DATABASE_URL` не задан, приложение использует локальную SQLite-базу в `instance/site.db`.

Для PostgreSQL используй строку вида:

```bash
export DATABASE_URL='postgresql+psycopg://user:password@127.0.0.1:15433/lowlands_vpn'
```

Если база не SQLite, приложение больше не будет молча создавать схему на старте.
Для production нужно явно прогнать миграции:

```bash
alembic upgrade head
```

Для локальной SQLite-разработки автоподъём схемы остаётся включённым.

### Переменные для VPN интеграции в Flask

- `VPN_AUTO_PROVISION`
- `VPN_SSH_HOST`
- `VPN_SSH_PORT`
- `VPN_SSH_USER`
- `VPN_SSH_KEY_PATH`
- `VPN_SSH_CONFIG_FILE`
- `VPN_SSH_CONNECT_TIMEOUT`
- `VPN_SSH_COMMAND_TIMEOUT`
- `VPN_SSH_COMMAND_RETRIES`
- `VPN_SSH_RETRY_BACKOFF_SECONDS`
- `VPN_SSH_STRICT_HOST_KEY_CHECKING`
- `VPN_REMOTE_ADD_SCRIPT`
- `VPN_REMOTE_REMOVE_SCRIPT`
- `VPN_REMOTE_BUILD_LINK_SCRIPT`
- `VPN_REMOTE_LIST_SCRIPT`
- `VPN_REMOTE_UPDATE_EMAIL_SCRIPT`

Если настроен `VPN_REMOTE_BUILD_LINK_SCRIPT`, Flask по умолчанию строит `vless://` ссылку
через удаленный скрипт на VPN-сервере. Это безопаснее, потому что `sid` и `sni` берутся
из живого Xray конфига, а не из локальной копии env-переменных.
Локальная сборка ссылки используется только как fallback, если удаленный builder не настроен.
По умолчанию `VPN_SSH_CONFIG_FILE=/dev/null`, чтобы не зависеть от системных SSH include-файлов.

### Переменные для email verification

- `EMAIL_VERIFICATION_ENABLED`
- `EMAIL_VERIFICATION_REQUIRED`

Подтверждение email выполняется вручную администратором.
При миграции существующие пользователи автоматически помечаются как уже подтвержденные.

### API + CSRF для session-auth

API работает через cookie-сессию и теперь требует CSRF-токен для всех mutating запросов (`POST`).

Получить токен:

```bash
curl -s http://127.0.0.1:5000/api/auth/csrf
```

Далее передавать его в заголовке `X-CSRFToken` для `/api/auth/login`, `/api/auth/logout`,
`/api/subscriptions/request`, `/api/devices`, `/api/devices/<id>/revoke`
и прочих `POST` endpoint'ов.

Health endpoints для мониторинга и smoke-проверок:

- `GET /health/live`
- `GET /health/ready`

## Тесты

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

## Миграции

Инициализирован Alembic baseline:

```bash
alembic upgrade head
```

Создать новую ревизию после изменения моделей:

```bash
alembic revision --autogenerate -m "your message"
```

Если Postgres находится на VPS и локально ты работаешь через SSH tunnel:

```bash
./scripts/start-postgres-tunnel.sh \
  --host 147.45.224.143 \
  --key-path /home/senamorsin/.ssh/lowlands_vpn_xray
```

Перенос текущих данных из `instance/site.db`:

```bash
./.venv/bin/python scripts/migrate-sqlite-to-postgres.py \
  --sqlite-path instance/site.db \
  --postgres-url "$DATABASE_URL"
```

## Эксплуатация

Подробный baseline и шаги по ограниченному SSH-доступу вынесены в:

- `docs/operations.md`

### Переезд сайта на новый Ubuntu сервер

Для быстрого "lift-and-shift" используй:

```bash
./scripts/migrate-web-server-ubuntu.sh \
  --host <NEW_SERVER_IP> \
  --root-key-path /path/to/root_key \
  --domain example.com \
  --vpn-key-path-local /home/senamorsin/.ssh/lowlands_vpn_xray \
  --old-host 194.87.130.123 \
  --old-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --old-db-name lowlands_vpn
```

Скрипт:

- устанавливает `nginx + postgresql + python` на новом сервере;
- копирует текущий проект;
- настраивает `systemd` сервис `lowlands-web`;
- создаёт `/etc/lowlands-web/flask.env`;
- прогоняет `alembic upgrade head`;
- перед деплоем создаёт rollback-бэкап в `/var/backups/lowlands-web` (можно переопределить `--backup-root`);
- (опционально) переносит данные из старой PostgreSQL через `pg_dump/pg_restore`.

### Обновление уже развернутого сервера

Для обычного релиза (без полного переезда) используй:

```bash
./scripts/update-web-server-ubuntu.sh \
  --host 194.87.130.123 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --domain localhost
```

Скрипт:

- загружает архив текущего проекта;
- делает pre-update backup в `/var/backups/lowlands-web`;
- обновляет код в `/srv/lowlands-web/app` (сохраняет `.venv` и `instance`);
- устанавливает зависимости;
- запускает `alembic upgrade head`;
- перезапускает `lowlands-web`;
- выполняет smoke-проверку.

После деплоя можно проверить сервер:

```bash
./scripts/smoke-test-web-server.sh \
  --host <NEW_SERVER_IP> \
  --root-key-path /path/to/root_key \
  --domain example.com
```

Если нужен откат:

```bash
./scripts/rollback-web-server-ubuntu.sh \
  --host <NEW_SERVER_IP> \
  --root-key-path /path/to/root_key \
  --backup-dir /var/backups/lowlands-web/<TIMESTAMP>
```

Быстрые команды:

```bash
./scripts/export-vpn-server-state.sh \
  --host 147.45.224.143 \
  --user root \
  --key-path /home/senamorsin/.ssh/lowlands_vpn_xray
```

```bash
./scripts/setup-restricted-vpn-ssh.sh \
  --host 147.45.224.143 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --app-public-key-path /home/senamorsin/.ssh/lowlands_vpn_xray.pub
```

```bash
./scripts/backup-postgres-ubuntu.sh \
  --host 194.87.130.123 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --db-name lowlands_vpn
```

```bash
./scripts/setup-authoritative-dns-ubuntu.sh \
  --host 194.87.130.123 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --zone your-domain.example
```

## VPN helper scripts

В репозитории есть серверные скрипты для Xray:

- `scripts/xray-add-client.sh`
- `scripts/xray-remove-client.sh`
- `scripts/xray-build-vless-link.sh`
- `scripts/xray-list-clients.sh`
- `scripts/xray-update-client-email.sh`

Они рассчитаны на запуск на VPN-сервере с Ubuntu и Xray под `systemd`.
Все скрипты автоматически загружают `/etc/lowlands-vpn/xray.env`, если файл существует.

### Что нужно на сервере

- `bash`
- `jq`
- `flock`
- `systemctl`
- `xray`

### Переменные окружения для Xray

- `XRAY_CONFIG_PATH` по умолчанию `/usr/local/etc/xray/config.json`
- `XRAY_SERVICE_NAME` по умолчанию `xray`
- `XRAY_INBOUND_TAG` по умолчанию `vless-reality`
- `XRAY_FLOW` по умолчанию `xtls-rprx-vision`
- `XRAY_LOCK_FILE` по умолчанию `/run/lock/xray-config.lock`
- `XRAY_API_SERVER` по умолчанию `127.0.0.1:10085`
- `XRAY_ENV_FILE` по умолчанию `/etc/lowlands-vpn/xray.env`

### Переменные окружения для VLESS ссылки

- `VLESS_HOST`
- `VLESS_PORT` по умолчанию `443`
- `VLESS_PBK`
- `VLESS_SNI`
- `VLESS_SID` опциональна: если не задана, `xray-build-vless-link.sh` читает `shortIds`
  из живого inbound в `config.json` и выбирает корректный `sid`
- `VLESS_FP` по умолчанию `chrome`
- `VLESS_FLOW` по умолчанию `xtls-rprx-vision`

### Рекомендуемая установка на сервер

```bash
sudo install -d -m 750 /etc/lowlands-vpn
sudo cp scripts/xray.env.example /etc/lowlands-vpn/xray.env
sudo chmod 640 /etc/lowlands-vpn/xray.env

sudo cp scripts/xray-add-client.sh /usr/local/sbin/xray-add-client
sudo cp scripts/xray-remove-client.sh /usr/local/sbin/xray-remove-client
sudo cp scripts/xray-build-vless-link.sh /usr/local/sbin/xray-build-vless-link
sudo cp scripts/xray-list-clients.sh /usr/local/sbin/xray-list-clients
sudo cp scripts/xray-update-client-email.sh /usr/local/sbin/xray-update-client-email
sudo chown root:root /usr/local/sbin/xray-add-client /usr/local/sbin/xray-remove-client /usr/local/sbin/xray-build-vless-link /usr/local/sbin/xray-list-clients /usr/local/sbin/xray-update-client-email
sudo chmod 750 /usr/local/sbin/xray-add-client /usr/local/sbin/xray-remove-client /usr/local/sbin/xray-build-vless-link /usr/local/sbin/xray-list-clients /usr/local/sbin/xray-update-client-email
```

После копирования поправьте значения в `/etc/lowlands-vpn/xray.env`.

### Примеры

Добавить клиента:

```bash
sudo ./scripts/xray-add-client.sh --email device-123@xray --name 'Work Laptop'
```

Добавить клиента с заранее заданным UUID:

```bash
sudo ./scripts/xray-add-client.sh \
  --email device-123@xray \
  --name 'Work Laptop' \
  --uuid 1430dff8-73ef-44bf-a9ce-09c3ef9b638b
```

`xray-add-client.sh` возвращает JSON с `uuid` и готовой `link`, поэтому Flask
может получить VLESS-ссылку в том же SSH вызове, без отдельного запроса после
перезапуска Xray.

Удалить клиента:

```bash
sudo ./scripts/xray-remove-client.sh --uuid 1430dff8-73ef-44bf-a9ce-09c3ef9b638b
```

Построить VLESS-ссылку:

```bash
export VLESS_HOST='147.45.224.143'
export VLESS_PBK='your-public-key'
export VLESS_SNI='www.yandex.ru'

./scripts/xray-build-vless-link.sh \
  --uuid 1430dff8-73ef-44bf-a9ce-09c3ef9b638b \
  --name 'pc-test-1'
```

Вывести live-список клиентов из Xray:

```bash
sudo ./scripts/xray-list-clients.sh --json
```

Переименовать Xray email (label) без изменения UUID:

```bash
sudo ./scripts/xray-update-client-email.sh \
  --uuid 1430dff8-73ef-44bf-a9ce-09c3ef9b638b \
  --email user1@xray \
  --json
```

Если в Xray позже будут включены `api` и `stats`, этот же helper начнет отдавать
и трафик по каждому клиенту.

- `SECRET_KEY`
- `DATABASE_URL`
- `LOG_LEVEL`
- `SESSION_COOKIE_SECURE`
- `REMEMBER_COOKIE_SECURE`
- `PREFERRED_URL_SCHEME`

Если `DATABASE_URL` не задан, приложение использует локальную SQLite-базу в `instance/site.db`.

### Переменные для VPN интеграции в Flask

- `VPN_AUTO_PROVISION`
- `VPN_SSH_HOST`
- `VPN_SSH_PORT`
- `VPN_SSH_USER`
- `VPN_SSH_KEY_PATH`
- `VPN_SSH_CONFIG_FILE`
- `VPN_SSH_CONNECT_TIMEOUT`
- `VPN_SSH_STRICT_HOST_KEY_CHECKING`
- `VPN_REMOTE_ADD_SCRIPT`
- `VPN_REMOTE_REMOVE_SCRIPT`
- `VPN_REMOTE_BUILD_LINK_SCRIPT`
- `VPN_REMOTE_LIST_SCRIPT`

Если настроен `VPN_REMOTE_BUILD_LINK_SCRIPT`, Flask по умолчанию строит `vless://` ссылку
через удаленный скрипт на VPN-сервере. Это безопаснее, потому что `sid` и `sni` берутся
из живого Xray конфига, а не из локальной копии env-переменных.
Локальная сборка ссылки используется только как fallback, если удаленный builder не настроен.
По умолчанию `VPN_SSH_CONFIG_FILE=/dev/null`, чтобы не зависеть от системных SSH include-файлов.

## Тесты

```bash
.venv/bin/pytest -q
```

## VPN helper scripts

В репозитории есть серверные скрипты для Xray:

- `scripts/xray-add-client.sh`
- `scripts/xray-remove-client.sh`
- `scripts/xray-build-vless-link.sh`
- `scripts/xray-list-clients.sh`

Они рассчитаны на запуск на VPN-сервере с Ubuntu и Xray под `systemd`.
Все три скрипта автоматически загружают `/etc/lowlands-vpn/xray.env`, если файл существует.

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
sudo chown root:root /usr/local/sbin/xray-add-client /usr/local/sbin/xray-remove-client /usr/local/sbin/xray-build-vless-link /usr/local/sbin/xray-list-clients
sudo chmod 750 /usr/local/sbin/xray-add-client /usr/local/sbin/xray-remove-client /usr/local/sbin/xray-build-vless-link /usr/local/sbin/xray-list-clients
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

Если в Xray позже будут включены `api` и `stats`, этот же helper начнет отдавать
и трафик по каждому клиенту.

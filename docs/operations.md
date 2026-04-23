# Эксплуатация

## Текущий VPN baseline

- VPN server: `147.45.224.143`
- Xray config: `/usr/local/etc/xray/config.json`
- Xray env: `/etc/lowlands-vpn/xray.env`
- Вспомогательные скрипты:
  - `/usr/local/sbin/xray-add-client`
  - `/usr/local/sbin/xray-remove-client`
  - `/usr/local/sbin/xray-build-vless-link`
  - `/usr/local/sbin/xray-list-clients`
  - `/usr/local/sbin/xray-update-client-email`
- Тег live inbound: `vless-reality-in`
- Xray API: `127.0.0.1:10085`

## Экспорт baseline-снимка

Используй локальный экспортный скрипт с машины, где запущен Flask:

```bash
./scripts/export-vpn-server-state.sh \
  --host 147.45.224.143 \
  --user root \
  --key-path /home/senamorsin/.ssh/lowlands_vpn_xray
```

Скрипт создаёт:

- a timestamped directory under `backups/vpn-server-state-*`
- a `.tar.gz` archive
- a matching `.sha256` checksum

В архив попадают:

- live `config.json`
- live `xray.env`
- IPv4/IPv6 sysctl state
- routes and listening sockets
- `systemctl status xray`
- recent `journalctl -u xray`
- `xray version`
- live Xray client list

Приватные SSH-ключи намеренно не копируются в архив. Храни их отдельно.

## Ограниченный SSH-пользователь

Задача - перестать давать Flask-приложению полный SSH-доступ под `root`.
Ограниченный пользователь может запускать только вспомогательные Xray-скрипты
через forced-command dispatcher и только через узкий allowlist в `sudoers`.

Устанавливается с машины, где запущен Flask:

```bash
./scripts/setup-restricted-vpn-ssh.sh \
  --host 147.45.224.143 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --app-public-key-path /home/senamorsin/.ssh/lowlands_vpn_xray.pub
```

Значения по умолчанию:

- restricted user: `lowlands-vpn`
- dispatcher path: `/usr/local/sbin/xray-ssh-dispatch`
- helper paths remain unchanged

После установки переведи Flask на этого пользователя:

```bash
export VPN_SSH_USER='lowlands-vpn'
```

## Проверка ограниченного доступа

Получить список клиентов:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-list-clients --json
```

Добавить временного клиента:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-add-client --email smoke-test@xray --uuid 00000000-0000-0000-0000-000000000099 --name smoke-test
```

Удалить его:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-remove-client --uuid 00000000-0000-0000-0000-000000000099
```

Переименовать Xray email без смены UUID:

```bash
ssh -F /dev/null -i /home/senamorsin/.ssh/lowlands_vpn_xray \
  lowlands-vpn@147.45.224.143 \
  /usr/local/sbin/xray-update-client-email --uuid 00000000-0000-0000-0000-000000000099 --email smoke-test-renamed@xray --json
```

Любая команда вне разрешённого списка должна отклоняться.

## Чек-лист восстановления

1. Подготовить Ubuntu.
2. Установить Xray и восстановить `/usr/local/etc/xray/config.json`.
3. Восстановить `/etc/lowlands-vpn/xray.env`.
4. Восстановить вспомогательные скрипты в `/usr/local/sbin`.
5. Восстановить sysctl-переопределения, если они были.
6. Проверить `systemctl status xray`.
7. Проверить `xray-list-clients --json`.
8. Запустить Flask с правильным `.flask-env`.

## Сайт: deploy/smoke/rollback

Переезд Flask-сайта на новый Ubuntu:

```bash
./scripts/migrate-web-server-ubuntu.sh \
  --host <NEW_SERVER_IP> \
  --root-key-path /path/to/root_key \
  --domain example.com \
  --vpn-key-path-local /home/senamorsin/.ssh/lowlands_vpn_xray
```

Перед заменой приложения скрипт создаёт rollback-бэкап на новом сервере
в `/var/backups/lowlands-web` (или в каталоге из `--backup-root`).

Регулярное обновление уже развернутого сервера:

```bash
./scripts/update-web-server-ubuntu.sh \
  --host 194.87.130.123 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --domain localhost
```

Smoke после деплоя:

```bash
./scripts/smoke-test-web-server.sh \
  --host <NEW_SERVER_IP> \
  --root-key-path /path/to/root_key \
  --domain example.com
```

Откат к конкретному backup:

```bash
./scripts/rollback-web-server-ubuntu.sh \
  --host <NEW_SERVER_IP> \
  --root-key-path /path/to/root_key \
  --backup-dir /var/backups/lowlands-web/<TIMESTAMP>
```

Снять backup PostgreSQL на проде:

```bash
./scripts/backup-postgres-ubuntu.sh \
  --host 194.87.130.123 \
  --root-key-path /home/senamorsin/.ssh/lowlands_vpn_xray \
  --db-name lowlands_vpn
```

#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  rollback-web-server-ubuntu.sh --host <server_ip> --root-key-path <path> --backup-dir <path>
                                [--root-user <user>] [--port <port>] [--ssh-config-file <path>]

Откатывает сайт на Ubuntu-сервере к резервной копии, созданной
`migrate-web-server-ubuntu.sh` в каталоге --backup-dir.
EOF
}

fail() {
    printf 'ОШИБКА: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Не найдена обязательная команда: $1"
}

HOST=""
ROOT_USER="root"
ROOT_KEY_PATH=""
PORT="22"
SSH_CONFIG_FILE="/dev/null"
BACKUP_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            HOST="${2:-}"
            shift 2
            ;;
        --root-user)
            ROOT_USER="${2:-}"
            shift 2
            ;;
        --root-key-path)
            ROOT_KEY_PATH="${2:-}"
            shift 2
            ;;
        --port)
            PORT="${2:-}"
            shift 2
            ;;
        --ssh-config-file)
            SSH_CONFIG_FILE="${2:-}"
            shift 2
            ;;
        --backup-dir)
            BACKUP_DIR="${2:-}"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Неизвестный аргумент: $1"
            ;;
    esac
done

[[ -n "$HOST" ]] || fail "Нужно указать --host"
[[ -n "$ROOT_KEY_PATH" ]] || fail "Нужно указать --root-key-path"
[[ -f "$ROOT_KEY_PATH" ]] || fail "SSH-ключ не найден: $ROOT_KEY_PATH"
[[ -n "$BACKUP_DIR" ]] || fail "Нужно указать --backup-dir"

require_command ssh

ssh_base=(
    ssh
    -F "$SSH_CONFIG_FILE"
    -i "$ROOT_KEY_PATH"
    -p "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
    "${ROOT_USER}@${HOST}"
)

printf 'Откатываю сервер %s к backup %s...\n' "$HOST" "$BACKUP_DIR"
"${ssh_base[@]}" "bash -s" -- "$BACKUP_DIR" <<'EOF'
set -euo pipefail

BACKUP_DIR="$1"
META_FILE="${BACKUP_DIR}/meta.env"
APP_ARCHIVE="${BACKUP_DIR}/app.tar.gz"

[[ -d "$BACKUP_DIR" ]] || {
    echo "Backup directory not found: ${BACKUP_DIR}" >&2
    exit 1
}
[[ -f "$META_FILE" ]] || {
    echo "Backup metadata not found: ${META_FILE}" >&2
    exit 1
}
[[ -f "$APP_ARCHIVE" ]] || {
    echo "Backup app archive not found: ${APP_ARCHIVE}" >&2
    exit 1
}

# shellcheck disable=SC1090
source "$META_FILE"

: "${APP_USER:?missing APP_USER in meta.env}"
: "${APP_GROUP:?missing APP_GROUP in meta.env}"
: "${APP_DIR:?missing APP_DIR in meta.env}"
: "${RUN_DIR:?missing RUN_DIR in meta.env}"
: "${SERVICE_NAME:?missing SERVICE_NAME in meta.env}"
: "${SERVICE_FILE:?missing SERVICE_FILE in meta.env}"
: "${ENV_FILE:?missing ENV_FILE in meta.env}"
: "${NGINX_SITE:?missing NGINX_SITE in meta.env}"
: "${NGINX_LINK:?missing NGINX_LINK in meta.env}"

install -d -o "$APP_USER" -g "$APP_GROUP" "$APP_DIR" "$RUN_DIR"
find "$APP_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
tar -xzf "$APP_ARCHIVE" -C "$APP_DIR"
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"

if [[ -f "${BACKUP_DIR}/flask.env" ]]; then
    install -d -m 750 -o root -g "$APP_GROUP" "$(dirname "$ENV_FILE")"
    install -m 640 -o root -g "$APP_GROUP" "${BACKUP_DIR}/flask.env" "$ENV_FILE"
fi

if [[ -f "${BACKUP_DIR}/${SERVICE_NAME}.service" ]]; then
    install -m 644 -o root -g root "${BACKUP_DIR}/${SERVICE_NAME}.service" "$SERVICE_FILE"
fi

if [[ -f "${BACKUP_DIR}/${SERVICE_NAME}.nginx.conf" ]]; then
    install -m 644 -o root -g root "${BACKUP_DIR}/${SERVICE_NAME}.nginx.conf" "$NGINX_SITE"
    ln -sf "$NGINX_SITE" "$NGINX_LINK"
fi

nginx -t
systemctl daemon-reload
systemctl enable --now postgresql nginx "$SERVICE_NAME"
systemctl restart nginx "$SERVICE_NAME"
systemctl is-active "$SERVICE_NAME" >/dev/null
systemctl is-active nginx >/dev/null
systemctl is-active postgresql >/dev/null
echo "ROLLBACK_OK"
EOF

printf 'Откат выполнен успешно.\n'

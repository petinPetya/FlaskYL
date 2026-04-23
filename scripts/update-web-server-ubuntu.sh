#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  update-web-server-ubuntu.sh --host <server_ip> --root-key-path <path>
                              [--source-dir <path>] [--root-user <user>] [--port <port>]
                              [--ssh-config-file <path>] [--app-dir <path>]
                              [--app-user <user>] [--app-group <group>]
                              [--service-name <name>] [--env-file <path>]
                              [--backup-root <path>] [--domain <domain>]
                              [--curl-timeout <sec>]
                              [--skip-backup] [--skip-pip] [--skip-migrate] [--skip-smoke]

Инкрементально обновляет уже развернутый Flask-сайт:
1) архивирует текущий локальный проект и загружает на сервер,
2) делает pre-update backup на сервере (по умолчанию),
3) обновляет код в APP_DIR (сохраняет .venv и instance),
4) ставит зависимости, запускает alembic upgrade head,
5) перезапускает systemd-сервис и (по умолчанию) делает smoke-проверку.
EOF
}

fail() {
    printf 'ОШИБКА: %s\n' "$1" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Не найдена обязательная команда: $1"
}

default_source_dir() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    cd "${script_dir}/.." && pwd
}

HOST=""
ROOT_USER="root"
ROOT_KEY_PATH=""
PORT="22"
SSH_CONFIG_FILE="/dev/null"
SOURCE_DIR="$(default_source_dir)"

APP_DIR="/srv/lowlands-web/app"
APP_USER="lowlands-web"
APP_GROUP=""
SERVICE_NAME="lowlands-web"
ENV_FILE="/etc/lowlands-web/flask.env"
BACKUP_ROOT="/var/backups/lowlands-web"

DOMAIN=""
CURL_TIMEOUT="8"

DO_BACKUP="true"
DO_PIP="true"
DO_MIGRATE="true"
RUN_SMOKE="true"

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
        --source-dir)
            SOURCE_DIR="${2:-}"
            shift 2
            ;;
        --app-dir)
            APP_DIR="${2:-}"
            shift 2
            ;;
        --app-user)
            APP_USER="${2:-}"
            shift 2
            ;;
        --app-group)
            APP_GROUP="${2:-}"
            shift 2
            ;;
        --service-name)
            SERVICE_NAME="${2:-}"
            shift 2
            ;;
        --env-file)
            ENV_FILE="${2:-}"
            shift 2
            ;;
        --backup-root)
            BACKUP_ROOT="${2:-}"
            shift 2
            ;;
        --domain)
            DOMAIN="${2:-}"
            shift 2
            ;;
        --curl-timeout)
            CURL_TIMEOUT="${2:-}"
            shift 2
            ;;
        --skip-backup)
            DO_BACKUP="false"
            shift
            ;;
        --skip-pip)
            DO_PIP="false"
            shift
            ;;
        --skip-migrate)
            DO_MIGRATE="false"
            shift
            ;;
        --skip-smoke)
            RUN_SMOKE="false"
            shift
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
[[ -d "$SOURCE_DIR" ]] || fail "Папка source-dir не найдена: $SOURCE_DIR"

if [[ -z "$APP_GROUP" ]]; then
    APP_GROUP="$APP_USER"
fi

require_command ssh
require_command scp
require_command tar

tmp_archive="$(mktemp /tmp/lowlands-web-update-src.XXXXXX.tar.gz)"
remote_archive="/tmp/lowlands-web-update-src.tar.gz"

cleanup() {
    rm -f "$tmp_archive"
}
trap cleanup EXIT

printf 'Собираю архив обновления...\n'
tar -C "$SOURCE_DIR" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='instance' \
    --exclude='backups' \
    --exclude='.codex' \
    --exclude='presentation.odp' \
    --exclude='.flask-env' \
    -czf "$tmp_archive" .

ssh_base=(
    ssh
    -F "$SSH_CONFIG_FILE"
    -i "$ROOT_KEY_PATH"
    -p "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
    "${ROOT_USER}@${HOST}"
)

scp_base=(
    scp
    -F "$SSH_CONFIG_FILE"
    -i "$ROOT_KEY_PATH"
    -P "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
)

printf 'Загружаю архив на сервер %s...\n' "$HOST"
"${scp_base[@]}" "$tmp_archive" "${ROOT_USER}@${HOST}:${remote_archive}"

printf 'Применяю обновление на сервере...\n'
"${ssh_base[@]}" "bash -s" -- \
    "$APP_DIR" \
    "$APP_USER" \
    "$APP_GROUP" \
    "$SERVICE_NAME" \
    "$ENV_FILE" \
    "$BACKUP_ROOT" \
    "$remote_archive" \
    "$DO_BACKUP" \
    "$DO_PIP" \
    "$DO_MIGRATE" <<'EOF'
set -euo pipefail

APP_DIR="$1"
APP_USER="$2"
APP_GROUP="$3"
SERVICE_NAME="$4"
ENV_FILE="$5"
BACKUP_ROOT="$6"
REMOTE_ARCHIVE="$7"
DO_BACKUP="$8"
DO_PIP="$9"
DO_MIGRATE="${10}"

[[ -d "$APP_DIR" ]] || {
    echo "APP_DIR не найден: $APP_DIR" >&2
    exit 1
}
[[ -f "$REMOTE_ARCHIVE" ]] || {
    echo "Архив обновления не найден: $REMOTE_ARCHIVE" >&2
    exit 1
}

id -u "$APP_USER" >/dev/null 2>&1 || {
    echo "Пользователь приложения не найден: $APP_USER" >&2
    exit 1
}

if [[ "$DO_BACKUP" == "true" ]]; then
    install -d -m 750 -o root -g root "$BACKUP_ROOT"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_archive="${BACKUP_ROOT}/app-preupdate-${stamp}.tar.gz"

    tar -C "$APP_DIR" --exclude='.venv' --exclude='instance' -czf "$backup_archive" .
    if [[ -f "$ENV_FILE" ]]; then
        cp -a "$ENV_FILE" "${BACKUP_ROOT}/flask-env-preupdate-${stamp}"
    fi
    echo "BACKUP_ARCHIVE=${backup_archive}"
fi

if [[ -f "$ENV_FILE" ]]; then
    chown root:"$APP_GROUP" "$ENV_FILE"
    chmod 640 "$ENV_FILE"
fi

if [[ ! -x "$APP_DIR/.venv/bin/pip" ]]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi

find "$APP_DIR" -mindepth 1 -maxdepth 1 ! -name '.venv' ! -name 'instance' -exec rm -rf {} +
tar -xzf "$REMOTE_ARCHIVE" -C "$APP_DIR"
rm -f "$REMOTE_ARCHIVE"
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"

if [[ "$DO_PIP" == "true" ]]; then
    sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
fi

if [[ "$DO_MIGRATE" == "true" ]]; then
    if [[ -f "$ENV_FILE" ]]; then
        sudo -u "$APP_USER" bash -lc "set -a; source '$ENV_FILE'; set +a; cd '$APP_DIR'; .venv/bin/alembic upgrade head"
    else
        sudo -u "$APP_USER" bash -lc "cd '$APP_DIR'; .venv/bin/alembic upgrade head"
    fi
fi

systemctl restart "$SERVICE_NAME"
systemctl is-active "$SERVICE_NAME" >/dev/null
systemctl is-active nginx >/dev/null
systemctl is-active postgresql >/dev/null
echo "DEPLOY_OK"
EOF

if [[ "$RUN_SMOKE" == "true" ]]; then
    printf 'Запускаю smoke-проверку...\n'
    smoke_command=(
        ./scripts/smoke-test-web-server.sh
        --host "$HOST"
        --root-key-path "$ROOT_KEY_PATH"
        --root-user "$ROOT_USER"
        --port "$PORT"
        --ssh-config-file "$SSH_CONFIG_FILE"
        --service-name "$SERVICE_NAME"
        --curl-timeout "$CURL_TIMEOUT"
    )
    if [[ -n "$DOMAIN" ]]; then
        smoke_command+=(--domain "$DOMAIN")
    fi
    "${smoke_command[@]}"
fi

printf '\nОбновление завершено.\n'
printf 'Хост: %s\n' "$HOST"
printf 'Сервис: %s\n' "$SERVICE_NAME"
printf 'Папка приложения: %s\n' "$APP_DIR"

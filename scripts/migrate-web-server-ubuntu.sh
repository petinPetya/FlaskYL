#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  migrate-web-server-ubuntu.sh --host <new_host> --root-key-path <path>
                               [--source-dir <path>] [--root-user <user>] [--port <port>]
                               [--ssh-config-file <path>] [--domain <domain>]
                               [--db-name <name>] [--db-user <name>] [--db-password <password>]
                               [--secret-key <value>]
                               [--vpn-key-path-local <path>]
                               [--vpn-ssh-host <host>] [--vpn-ssh-user <user>] [--vpn-ssh-port <port>]
                               [--old-host <old_host>] [--old-key-path <path>] [--old-db-name <name>]
                               [--backup-root <path>] [--skip-remote-backup]
                               [--disable-ufw]

Скрипт делает "переезд" Flask-сайта на новый Ubuntu сервер:
1) устанавливает системные зависимости (nginx/postgresql/python),
2) копирует текущий проект,
3) настраивает systemd + nginx,
4) создаёт env-файл приложения,
5) выполняет alembic upgrade head.

Опционально может забрать дамп БД с текущего сервера и восстановить на новом.
Перед заменой файлов на новом сервере создаётся rollback-бэкап
в --backup-root (по умолчанию /var/backups/lowlands-web).
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

random_hex() {
    openssl rand -hex "$1"
}

validate_identifier() {
    local value="$1"
    local name="$2"
    if [[ ! "$value" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        fail "${name} должен содержать только буквы/цифры/underscore и не начинаться с цифры."
    fi
}

NEW_HOST=""
ROOT_USER="root"
ROOT_KEY_PATH=""
PORT="22"
SSH_CONFIG_FILE="/dev/null"
SOURCE_DIR="$(default_source_dir)"

APP_USER="lowlands-web"
APP_BASE_DIR="/srv/lowlands-web"
APP_DIR="${APP_BASE_DIR}/app"
RUN_DIR="${APP_BASE_DIR}/run"
SERVICE_NAME="lowlands-web"

DB_NAME="lowlands_vpn"
DB_USER="lowlands_vpn_app"
DB_PASSWORD=""
SECRET_KEY=""

DOMAIN=""
PREFERRED_URL_SCHEME=""
SESSION_COOKIE_SECURE=""
REMEMBER_COOKIE_SECURE=""
LOG_LEVEL="INFO"
BOOTSTRAP_SCHEMA_ON_STARTUP="false"

VPN_AUTO_PROVISION="true"
VPN_SSH_HOST="147.45.224.143"
VPN_SSH_PORT="22"
VPN_SSH_USER="lowlands-vpn"
VPN_SSH_KEY_PATH_REMOTE="/etc/lowlands-web/ssh/lowlands_vpn_xray"
VPN_SSH_KEY_PATH_LOCAL=""
VPN_SSH_CONFIG_FILE="/dev/null"
VPN_SSH_CONNECT_TIMEOUT="10"
VPN_SSH_COMMAND_TIMEOUT="20"
VPN_SSH_STRICT_HOST_KEY_CHECKING="true"
VPN_REMOTE_ADD_SCRIPT="/usr/local/sbin/xray-add-client"
VPN_REMOTE_REMOVE_SCRIPT="/usr/local/sbin/xray-remove-client"
VPN_REMOTE_BUILD_LINK_SCRIPT="/usr/local/sbin/xray-build-vless-link"
VPN_REMOTE_LIST_SCRIPT="/usr/local/sbin/xray-list-clients"
VPN_REMOTE_UPDATE_EMAIL_SCRIPT="/usr/local/sbin/xray-update-client-email"
VLESS_HOST="147.45.224.143"
VLESS_PORT="443"
VLESS_PBK=""
VLESS_SNI="www.yandex.ru"
VLESS_SID=""
VLESS_FP="chrome"
VLESS_FLOW="xtls-rprx-vision"

OLD_HOST=""
OLD_KEY_PATH=""
OLD_DB_NAME=""

ENABLE_UFW="true"
BACKUP_ROOT="/var/backups/lowlands-web"
CREATE_REMOTE_BACKUP="true"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            NEW_HOST="${2:-}"
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
        --domain)
            DOMAIN="${2:-}"
            shift 2
            ;;
        --db-name)
            DB_NAME="${2:-}"
            shift 2
            ;;
        --db-user)
            DB_USER="${2:-}"
            shift 2
            ;;
        --db-password)
            DB_PASSWORD="${2:-}"
            shift 2
            ;;
        --secret-key)
            SECRET_KEY="${2:-}"
            shift 2
            ;;
        --vpn-key-path-local)
            VPN_SSH_KEY_PATH_LOCAL="${2:-}"
            shift 2
            ;;
        --vpn-ssh-host)
            VPN_SSH_HOST="${2:-}"
            shift 2
            ;;
        --vpn-ssh-user)
            VPN_SSH_USER="${2:-}"
            shift 2
            ;;
        --vpn-ssh-port)
            VPN_SSH_PORT="${2:-}"
            shift 2
            ;;
        --old-host)
            OLD_HOST="${2:-}"
            shift 2
            ;;
        --old-key-path)
            OLD_KEY_PATH="${2:-}"
            shift 2
            ;;
        --old-db-name)
            OLD_DB_NAME="${2:-}"
            shift 2
            ;;
        --disable-ufw)
            ENABLE_UFW="false"
            shift
            ;;
        --backup-root)
            BACKUP_ROOT="${2:-}"
            shift 2
            ;;
        --skip-remote-backup)
            CREATE_REMOTE_BACKUP="false"
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

[[ -n "$NEW_HOST" ]] || fail "Нужно указать --host"
[[ -n "$ROOT_KEY_PATH" ]] || fail "Нужно указать --root-key-path"
[[ -f "$ROOT_KEY_PATH" ]] || fail "SSH-ключ не найден: $ROOT_KEY_PATH"
[[ -d "$SOURCE_DIR" ]] || fail "Папка source-dir не найдена: $SOURCE_DIR"

if [[ -n "$VPN_SSH_KEY_PATH_LOCAL" && ! -f "$VPN_SSH_KEY_PATH_LOCAL" ]]; then
    fail "VPN SSH ключ не найден: $VPN_SSH_KEY_PATH_LOCAL"
fi

if [[ -n "$OLD_HOST" ]]; then
    [[ -n "$OLD_KEY_PATH" ]] || fail "Для --old-host нужно указать --old-key-path"
    [[ -f "$OLD_KEY_PATH" ]] || fail "SSH-ключ старого сервера не найден: $OLD_KEY_PATH"
fi

if [[ -z "$OLD_DB_NAME" ]]; then
    OLD_DB_NAME="$DB_NAME"
fi

require_command ssh
require_command scp
require_command tar
require_command openssl

validate_identifier "$DB_NAME" "DB_NAME"
validate_identifier "$DB_USER" "DB_USER"

if [[ -z "$DB_PASSWORD" ]]; then
    DB_PASSWORD="$(random_hex 16)"
fi
if [[ -z "$SECRET_KEY" ]]; then
    SECRET_KEY="$(random_hex 32)"
fi

if [[ -z "$PREFERRED_URL_SCHEME" ]]; then
    if [[ -n "$DOMAIN" ]]; then
        PREFERRED_URL_SCHEME="https"
    else
        PREFERRED_URL_SCHEME="http"
    fi
fi
if [[ -z "$SESSION_COOKIE_SECURE" ]]; then
    [[ "$PREFERRED_URL_SCHEME" == "https" ]] && SESSION_COOKIE_SECURE="1" || SESSION_COOKIE_SECURE="0"
fi
if [[ -z "$REMEMBER_COOKIE_SECURE" ]]; then
    [[ "$PREFERRED_URL_SCHEME" == "https" ]] && REMEMBER_COOKIE_SECURE="1" || REMEMBER_COOKIE_SECURE="0"
fi

tmp_archive="$(mktemp /tmp/lowlands-web-src.XXXXXX.tar.gz)"
tmp_db_dump=""
remote_archive="/tmp/lowlands-web-src.tar.gz"
remote_vpn_key_tmp="/tmp/lowlands-vpn-ssh.key"
remote_db_dump="/tmp/lowlands-web-db.dump"

cleanup() {
    rm -f "$tmp_archive"
    if [[ -n "$tmp_db_dump" ]]; then
        rm -f "$tmp_db_dump"
    fi
}
trap cleanup EXIT

printf 'Подготавливаю архив проекта...\n'
tar -C "$SOURCE_DIR" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='instance' \
    --exclude='backups' \
    --exclude='presentation.odp' \
    --exclude='.codex' \
    -czf "$tmp_archive" .

if [[ -n "$OLD_HOST" ]]; then
    printf 'Снимаю дамп PostgreSQL со старого сервера %s...\n' "$OLD_HOST"
    tmp_db_dump="$(mktemp /tmp/lowlands-web-db.XXXXXX.dump)"
    ssh -F "$SSH_CONFIG_FILE" \
        -i "$OLD_KEY_PATH" \
        -p "$PORT" \
        -o BatchMode=yes \
        -o ConnectTimeout=10 \
        "${ROOT_USER}@${OLD_HOST}" \
        "sudo -u postgres pg_dump --format=custom --dbname='${OLD_DB_NAME}'" > "$tmp_db_dump"
fi

ssh_base=(
    ssh
    -F "$SSH_CONFIG_FILE"
    -i "$ROOT_KEY_PATH"
    -p "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
    "${ROOT_USER}@${NEW_HOST}"
)

scp_base=(
    scp
    -F "$SSH_CONFIG_FILE"
    -i "$ROOT_KEY_PATH"
    -P "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
)

printf 'Загружаю архив на новый сервер %s...\n' "$NEW_HOST"
"${scp_base[@]}" "$tmp_archive" "${ROOT_USER}@${NEW_HOST}:${remote_archive}"

if [[ -n "$VPN_SSH_KEY_PATH_LOCAL" ]]; then
    printf 'Загружаю VPN SSH ключ на новый сервер...\n'
    "${scp_base[@]}" "$VPN_SSH_KEY_PATH_LOCAL" "${ROOT_USER}@${NEW_HOST}:${remote_vpn_key_tmp}"
fi

if [[ -n "$tmp_db_dump" ]]; then
    printf 'Загружаю дамп БД на новый сервер...\n'
    "${scp_base[@]}" "$tmp_db_dump" "${ROOT_USER}@${NEW_HOST}:${remote_db_dump}"
fi

printf 'Провожу настройку на новом сервере...\n'
"${ssh_base[@]}" "bash -s" -- \
    "$APP_USER" \
    "$APP_BASE_DIR" \
    "$APP_DIR" \
    "$RUN_DIR" \
    "$SERVICE_NAME" \
    "$remote_archive" \
    "$DB_NAME" \
    "$DB_USER" \
    "$DB_PASSWORD" \
    "$SECRET_KEY" \
    "$DOMAIN" \
    "$PREFERRED_URL_SCHEME" \
    "$SESSION_COOKIE_SECURE" \
    "$REMEMBER_COOKIE_SECURE" \
    "$LOG_LEVEL" \
    "$BOOTSTRAP_SCHEMA_ON_STARTUP" \
    "$VPN_AUTO_PROVISION" \
    "$VPN_SSH_HOST" \
    "$VPN_SSH_PORT" \
    "$VPN_SSH_USER" \
    "$VPN_SSH_KEY_PATH_REMOTE" \
    "$VPN_SSH_CONFIG_FILE" \
    "$VPN_SSH_CONNECT_TIMEOUT" \
    "$VPN_SSH_COMMAND_TIMEOUT" \
    "$VPN_SSH_STRICT_HOST_KEY_CHECKING" \
    "$VPN_REMOTE_ADD_SCRIPT" \
    "$VPN_REMOTE_REMOVE_SCRIPT" \
    "$VPN_REMOTE_BUILD_LINK_SCRIPT" \
    "$VPN_REMOTE_LIST_SCRIPT" \
    "$VPN_REMOTE_UPDATE_EMAIL_SCRIPT" \
    "$VLESS_HOST" \
    "$VLESS_PORT" \
    "$VLESS_PBK" \
    "$VLESS_SNI" \
    "$VLESS_SID" \
    "$VLESS_FP" \
    "$VLESS_FLOW" \
    "$remote_vpn_key_tmp" \
    "$remote_db_dump" \
    "$ENABLE_UFW" \
    "$BACKUP_ROOT" \
    "$CREATE_REMOTE_BACKUP" <<'EOF'
set -euo pipefail

APP_USER="$1"
APP_BASE_DIR="$2"
APP_DIR="$3"
RUN_DIR="$4"
SERVICE_NAME="$5"
REMOTE_ARCHIVE="$6"
DB_NAME="$7"
DB_USER="$8"
DB_PASSWORD="$9"
SECRET_KEY="${10}"
DOMAIN="${11}"
PREFERRED_URL_SCHEME="${12}"
SESSION_COOKIE_SECURE="${13}"
REMEMBER_COOKIE_SECURE="${14}"
LOG_LEVEL="${15}"
BOOTSTRAP_SCHEMA_ON_STARTUP="${16}"
VPN_AUTO_PROVISION="${17}"
VPN_SSH_HOST="${18}"
VPN_SSH_PORT="${19}"
VPN_SSH_USER="${20}"
VPN_SSH_KEY_PATH_REMOTE="${21}"
VPN_SSH_CONFIG_FILE="${22}"
VPN_SSH_CONNECT_TIMEOUT="${23}"
VPN_SSH_COMMAND_TIMEOUT="${24}"
VPN_SSH_STRICT_HOST_KEY_CHECKING="${25}"
VPN_REMOTE_ADD_SCRIPT="${26}"
VPN_REMOTE_REMOVE_SCRIPT="${27}"
VPN_REMOTE_BUILD_LINK_SCRIPT="${28}"
VPN_REMOTE_LIST_SCRIPT="${29}"
VPN_REMOTE_UPDATE_EMAIL_SCRIPT="${30}"
VLESS_HOST="${31}"
VLESS_PORT="${32}"
VLESS_PBK="${33}"
VLESS_SNI="${34}"
VLESS_SID="${35}"
VLESS_FP="${36}"
VLESS_FLOW="${37}"
REMOTE_VPN_KEY_TMP="${38}"
REMOTE_DB_DUMP="${39}"
ENABLE_UFW="${40}"
BACKUP_ROOT="${41}"
CREATE_REMOTE_BACKUP="${42}"

APP_GROUP="$APP_USER"
ENV_DIR="/etc/lowlands-web"
ENV_FILE="${ENV_DIR}/flask.env"
VPN_KEY_DIR="${ENV_DIR}/ssh"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    nginx \
    postgresql \
    postgresql-contrib \
    python3 \
    python3-pip \
    python3-venv \
    ufw

if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$APP_BASE_DIR" --shell /bin/bash "$APP_USER"
fi

install -d -o "$APP_USER" -g "$APP_GROUP" "$APP_BASE_DIR" "$APP_DIR" "$RUN_DIR"
install -d -m 750 -o root -g "$APP_GROUP" "$ENV_DIR" "$VPN_KEY_DIR"

DEPLOY_BACKUP_DIR=""
if [[ "$CREATE_REMOTE_BACKUP" == "true" ]]; then
    backup_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    DEPLOY_BACKUP_DIR="${BACKUP_ROOT}/${backup_stamp}"
    install -d -m 750 -o root -g root "$DEPLOY_BACKUP_DIR"

    tar -C "$APP_DIR" -czf "${DEPLOY_BACKUP_DIR}/app.tar.gz" .
    [[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "${DEPLOY_BACKUP_DIR}/flask.env"
    [[ -f "$SERVICE_FILE" ]] && cp -a "$SERVICE_FILE" "${DEPLOY_BACKUP_DIR}/${SERVICE_NAME}.service"
    [[ -f "$NGINX_SITE" ]] && cp -a "$NGINX_SITE" "${DEPLOY_BACKUP_DIR}/${SERVICE_NAME}.nginx.conf"

    cat > "${DEPLOY_BACKUP_DIR}/meta.env" <<EOF_META
APP_USER='${APP_USER}'
APP_GROUP='${APP_GROUP}'
APP_BASE_DIR='${APP_BASE_DIR}'
APP_DIR='${APP_DIR}'
RUN_DIR='${RUN_DIR}'
SERVICE_NAME='${SERVICE_NAME}'
SERVICE_FILE='${SERVICE_FILE}'
ENV_FILE='${ENV_FILE}'
NGINX_SITE='${NGINX_SITE}'
NGINX_LINK='${NGINX_LINK}'
EOF_META
fi

find "$APP_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
tar -xzf "$REMOTE_ARCHIVE" -C "$APP_DIR"
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
rm -f "$REMOTE_ARCHIVE"

sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

db_password_sql="${DB_PASSWORD//\'/\'\'}"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN PASSWORD '${db_password_sql}';
    ELSE
        ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${db_password_sql}';
    END IF;
END
\$\$;
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}') THEN
        CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};
    END IF;
END
\$\$;
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

if [[ -f "$REMOTE_DB_DUMP" ]]; then
    sudo -u postgres pg_restore \
        --clean \
        --if-exists \
        --no-owner \
        --dbname="$DB_NAME" \
        "$REMOTE_DB_DUMP"
    rm -f "$REMOTE_DB_DUMP"
fi

if [[ -f "$REMOTE_VPN_KEY_TMP" ]]; then
    install -m 600 -o "$APP_USER" -g "$APP_GROUP" "$REMOTE_VPN_KEY_TMP" "$VPN_SSH_KEY_PATH_REMOTE"
    rm -f "$REMOTE_VPN_KEY_TMP"
fi

cat > "$ENV_FILE" <<EOF_ENV
SECRET_KEY='${SECRET_KEY}'
DATABASE_URL='postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}'
BOOTSTRAP_SCHEMA_ON_STARTUP=${BOOTSTRAP_SCHEMA_ON_STARTUP}
LOG_LEVEL='${LOG_LEVEL}'
PREFERRED_URL_SCHEME='${PREFERRED_URL_SCHEME}'
SESSION_COOKIE_SECURE='${SESSION_COOKIE_SECURE}'
REMEMBER_COOKIE_SECURE='${REMEMBER_COOKIE_SECURE}'
VPN_AUTO_PROVISION=${VPN_AUTO_PROVISION}
VPN_SSH_HOST='${VPN_SSH_HOST}'
VPN_SSH_PORT=${VPN_SSH_PORT}
VPN_SSH_USER='${VPN_SSH_USER}'
VPN_SSH_KEY_PATH='${VPN_SSH_KEY_PATH_REMOTE}'
VPN_SSH_CONFIG_FILE='${VPN_SSH_CONFIG_FILE}'
VPN_SSH_CONNECT_TIMEOUT=${VPN_SSH_CONNECT_TIMEOUT}
VPN_SSH_COMMAND_TIMEOUT=${VPN_SSH_COMMAND_TIMEOUT}
VPN_SSH_STRICT_HOST_KEY_CHECKING=${VPN_SSH_STRICT_HOST_KEY_CHECKING}
VPN_REMOTE_ADD_SCRIPT='${VPN_REMOTE_ADD_SCRIPT}'
VPN_REMOTE_REMOVE_SCRIPT='${VPN_REMOTE_REMOVE_SCRIPT}'
VPN_REMOTE_BUILD_LINK_SCRIPT='${VPN_REMOTE_BUILD_LINK_SCRIPT}'
VPN_REMOTE_LIST_SCRIPT='${VPN_REMOTE_LIST_SCRIPT}'
VPN_REMOTE_UPDATE_EMAIL_SCRIPT='${VPN_REMOTE_UPDATE_EMAIL_SCRIPT}'
VLESS_HOST='${VLESS_HOST}'
VLESS_PORT=${VLESS_PORT}
VLESS_PBK='${VLESS_PBK}'
VLESS_SNI='${VLESS_SNI}'
VLESS_SID='${VLESS_SID}'
VLESS_FP='${VLESS_FP}'
VLESS_FLOW='${VLESS_FLOW}'
EMAIL_VERIFICATION_ENABLED='1'
EMAIL_VERIFICATION_REQUIRED='0'
EOF_ENV
chown root:"$APP_GROUP" "$ENV_FILE"
chmod 640 "$ENV_FILE"

cat > "$SERVICE_FILE" <<EOF_SERVICE
[Unit]
Description=Lowlands VPN Flask application
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/.venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 --access-logfile - --error-logfile - app:app
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${APP_DIR}/instance ${RUN_DIR}

[Install]
WantedBy=multi-user.target
EOF_SERVICE

server_name="_"
if [[ -n "$DOMAIN" ]]; then
    server_name="$DOMAIN"
fi

cat > "$NGINX_SITE" <<EOF_NGINX
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name ${server_name};

    access_log /var/log/nginx/${SERVICE_NAME}.access.log;
    error_log /var/log/nginx/${SERVICE_NAME}.error.log;

    location /static/ {
        alias ${APP_DIR}/static/;
        expires 1h;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60;
    }
}
EOF_NGINX

ln -sf "$NGINX_SITE" "$NGINX_LINK"
rm -f /etc/nginx/sites-enabled/default

sudo -u "$APP_USER" bash -lc "
    set -a
    source '$ENV_FILE'
    set +a
    cd '$APP_DIR'
    .venv/bin/alembic upgrade head
"

nginx -t
systemctl daemon-reload
systemctl enable --now postgresql nginx "$SERVICE_NAME"
systemctl restart nginx "$SERVICE_NAME"

if [[ "$ENABLE_UFW" == "true" ]]; then
    ufw allow OpenSSH
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable
fi

systemctl is-active "$SERVICE_NAME" >/dev/null
systemctl is-active nginx >/dev/null
systemctl is-active postgresql >/dev/null
if [[ -n "$DEPLOY_BACKUP_DIR" ]]; then
    echo "BACKUP_DIR=${DEPLOY_BACKUP_DIR}"
fi
echo "OK"
EOF

printf '\nГотово.\n'
printf 'Новый сервер: %s\n' "$NEW_HOST"
printf 'Systemd service: %s\n' "$SERVICE_NAME"
printf 'Env file: /etc/lowlands-web/flask.env\n'
printf 'DB: %s (user: %s)\n' "$DB_NAME" "$DB_USER"
if [[ "$CREATE_REMOTE_BACKUP" == "true" ]]; then
    printf 'Rollback backups: %s\n' "$BACKUP_ROOT"
    printf 'To rollback: ./scripts/rollback-web-server-ubuntu.sh --host %s --root-key-path %s --backup-dir <dir>\n' "$NEW_HOST" "$ROOT_KEY_PATH"
fi
if [[ -n "$DOMAIN" ]]; then
    printf 'Домен в nginx: %s\n' "$DOMAIN"
else
    printf 'Домен не задан: nginx настроен на server_name _\n'
fi
if [[ "$SESSION_COOKIE_SECURE" != "1" ]]; then
    printf 'ВНИМАНИЕ: cookie secure отключены. Для production HTTPS переключи их в 1.\n'
fi

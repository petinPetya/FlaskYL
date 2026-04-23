#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  smoke-test-web-server.sh --host <server_ip> --root-key-path <path>
                           [--root-user <user>] [--port <port>] [--ssh-config-file <path>]
                           [--service-name <name>] [--live-path <path>] [--ready-path <path>]
                           [--domain <domain>] [--curl-timeout <sec>]

Скрипт проверяет:
1) что сервисы lowlands-web/nginx/postgresql запущены;
2) что health endpoints отвечают локально на сервере;
3) что главная страница отдаёт HTTP 200 через nginx.
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
SERVICE_NAME="lowlands-web"
LIVE_PATH="/health/live"
READY_PATH="/health/ready"
DOMAIN=""
CURL_TIMEOUT="8"

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
        --service-name)
            SERVICE_NAME="${2:-}"
            shift 2
            ;;
        --live-path)
            LIVE_PATH="${2:-}"
            shift 2
            ;;
        --ready-path)
            READY_PATH="${2:-}"
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

printf 'Запускаю smoke-проверку на %s...\n' "$HOST"
"${ssh_base[@]}" "bash -s" -- \
    "$SERVICE_NAME" \
    "$LIVE_PATH" \
    "$READY_PATH" \
    "$DOMAIN" \
    "$CURL_TIMEOUT" <<'EOF'
set -euo pipefail

SERVICE_NAME="${1:-lowlands-web}"
LIVE_PATH="${2:-/health/live}"
READY_PATH="${3:-/health/ready}"
DOMAIN="${4:-}"
CURL_TIMEOUT="${5:-8}"

systemctl is-active "$SERVICE_NAME" >/dev/null
systemctl is-active nginx >/dev/null
systemctl is-active postgresql >/dev/null

live_payload="$(curl -fsS --max-time "$CURL_TIMEOUT" "http://127.0.0.1${LIVE_PATH}")"
ready_payload="$(curl -fsS --max-time "$CURL_TIMEOUT" "http://127.0.0.1${READY_PATH}")"

if [[ -n "$DOMAIN" ]]; then
    index_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$CURL_TIMEOUT" -H "Host: ${DOMAIN}" "http://127.0.0.1/")"
else
    index_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$CURL_TIMEOUT" "http://127.0.0.1/")"
fi

if [[ "$index_code" != "200" ]]; then
    echo "INDEX_HTTP_CODE=${index_code}"
    exit 1
fi

echo "LOWLANDS_SERVICE=active"
echo "NGINX_SERVICE=active"
echo "POSTGRES_SERVICE=active"
echo "LIVE_PAYLOAD=${live_payload}"
echo "READY_PAYLOAD=${ready_payload}"
echo "INDEX_HTTP_CODE=${index_code}"
echo "SMOKE_OK"
EOF

printf 'Smoke-проверка пройдена.\n'

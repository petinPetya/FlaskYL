#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  start-postgres-tunnel.sh --host <host> --key-path <path>
                           [--user <user>] [--local-port <port>] [--remote-port <port>]
                           [--ssh-config-file <path>]

Поднимает SSH-туннель от localhost к локальному порту PostgreSQL на VPS.
EOF
}

fail() {
    printf 'ОШИБКА: %s\n' "$1" >&2
    exit 1
}

HOST=""
USER="root"
KEY_PATH=""
LOCAL_PORT="15433"
REMOTE_PORT="5432"
SSH_CONFIG_FILE="/dev/null"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)
            HOST="${2:-}"
            shift 2
            ;;
        --user)
            USER="${2:-}"
            shift 2
            ;;
        --key-path)
            KEY_PATH="${2:-}"
            shift 2
            ;;
        --local-port)
            LOCAL_PORT="${2:-}"
            shift 2
            ;;
        --remote-port)
            REMOTE_PORT="${2:-}"
            shift 2
            ;;
        --ssh-config-file)
            SSH_CONFIG_FILE="${2:-}"
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
[[ -n "$KEY_PATH" ]] || fail "Нужно указать --key-path"
[[ -f "$KEY_PATH" ]] || fail "SSH-ключ не найден: $KEY_PATH"

ssh -F "$SSH_CONFIG_FILE" \
    -i "$KEY_PATH" \
    -f \
    -N \
    -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
    -o ExitOnForwardFailure=yes \
    -o BatchMode=yes \
    -o ConnectTimeout=10 \
    "${USER}@${HOST}"

printf 'Туннель поднят: 127.0.0.1:%s -> %s:127.0.0.1:%s\n' "$LOCAL_PORT" "$HOST" "$REMOTE_PORT"

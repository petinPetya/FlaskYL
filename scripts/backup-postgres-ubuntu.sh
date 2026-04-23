#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  backup-postgres-ubuntu.sh --host <server_ip> --root-key-path <path> --db-name <name>
                            [--root-user <user>] [--port <port>] [--ssh-config-file <path>]
                            [--backup-dir <path>] [--keep <count>]

Создаёт timestamped backup PostgreSQL на удалённом Ubuntu-сервере.
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
DB_NAME=""
BACKUP_DIR="/var/backups/lowlands-web-db"
KEEP_COUNT="10"

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
        --db-name)
            DB_NAME="${2:-}"
            shift 2
            ;;
        --backup-dir)
            BACKUP_DIR="${2:-}"
            shift 2
            ;;
        --keep)
            KEEP_COUNT="${2:-}"
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
[[ -n "$DB_NAME" ]] || fail "Нужно указать --db-name"

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

printf 'Создаю backup базы %s на сервере %s...\n' "$DB_NAME" "$HOST"
"${ssh_base[@]}" "bash -s" -- "$DB_NAME" "$BACKUP_DIR" "$KEEP_COUNT" <<'EOF'
set -euo pipefail

DB_NAME="$1"
BACKUP_DIR="$2"
KEEP_COUNT="$3"

install -d -m 750 -o root -g root "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_file="${BACKUP_DIR}/${DB_NAME}-${timestamp}.dump"

sudo -u postgres pg_dump --format=custom --dbname="$DB_NAME" --file="$dump_file"
chmod 640 "$dump_file"

if [[ "$KEEP_COUNT" =~ ^[0-9]+$ ]] && [[ "$KEEP_COUNT" -gt 0 ]]; then
    mapfile -t old_backups < <(ls -1t "${BACKUP_DIR}/${DB_NAME}-"*.dump 2>/dev/null | tail -n +"$((KEEP_COUNT + 1))")
    if [[ "${#old_backups[@]}" -gt 0 ]]; then
        rm -f "${old_backups[@]}"
    fi
fi

echo "BACKUP_FILE=${dump_file}"
echo "BACKUP_OK"
EOF

printf 'Backup завершён.\n'

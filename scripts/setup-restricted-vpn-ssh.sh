#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  setup-restricted-vpn-ssh.sh --host <host> --root-key-path <path>
                              --app-public-key-path <path>
                              [--root-user <user>] [--restricted-user <user>]
                              [--port <port>] [--ssh-config-file <path>]
                              [--dispatcher-path <path>]

Создаёт на VPN-сервере ограниченного SSH-пользователя и устанавливает
forced-command dispatcher, чтобы Flask-приложению больше не был нужен
неограниченный SSH-доступ под root.
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
APP_PUBLIC_KEY_PATH=""
RESTRICTED_USER="lowlands-vpn"
PORT="22"
SSH_CONFIG_FILE="/dev/null"
DISPATCHER_PATH="/usr/local/sbin/xray-ssh-dispatch"

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
        --app-public-key-path)
            APP_PUBLIC_KEY_PATH="${2:-}"
            shift 2
            ;;
        --restricted-user)
            RESTRICTED_USER="${2:-}"
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
        --dispatcher-path)
            DISPATCHER_PATH="${2:-}"
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
[[ -n "$APP_PUBLIC_KEY_PATH" ]] || fail "Нужно указать --app-public-key-path"
[[ -f "$ROOT_KEY_PATH" ]] || fail "SSH-ключ не найден: $ROOT_KEY_PATH"
[[ -f "$APP_PUBLIC_KEY_PATH" ]] || fail "Публичный ключ не найден: $APP_PUBLIC_KEY_PATH"

require_command ssh
require_command scp
require_command base64

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dispatcher_source="${script_dir}/xray-ssh-dispatch.py"
[[ -f "$dispatcher_source" ]] || fail "Dispatcher не найден: $dispatcher_source"

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

remote_tmp="/tmp/xray-ssh-dispatch.py"
pubkey_base64="$(base64 -w0 < "$APP_PUBLIC_KEY_PATH")"

"${scp_base[@]}" "$dispatcher_source" "${ROOT_USER}@${HOST}:${remote_tmp}"

"${ssh_base[@]}" "RESTRICTED_USER=$(printf '%q' "$RESTRICTED_USER") DISPATCHER_PATH=$(printf '%q' "$DISPATCHER_PATH") PUBKEY_BASE64=$(printf '%q' "$pubkey_base64") bash -s" <<'EOF'
set -euo pipefail

home_dir="/home/${RESTRICTED_USER}"
ssh_dir="${home_dir}/.ssh"
authorized_keys="${ssh_dir}/authorized_keys"

if ! id -u "$RESTRICTED_USER" >/dev/null 2>&1; then
    useradd --create-home --home-dir "$home_dir" --shell /bin/bash "$RESTRICTED_USER"
fi

usermod --shell /bin/bash "$RESTRICTED_USER"

install -m 755 /tmp/xray-ssh-dispatch.py "$DISPATCHER_PATH"
rm -f /tmp/xray-ssh-dispatch.py

install -d -m 700 -o "$RESTRICTED_USER" -g "$RESTRICTED_USER" "$ssh_dir"

if [[ -f "$authorized_keys" ]]; then
    cp -a "$authorized_keys" "${authorized_keys}.bak.$(date +%Y%m%d%H%M%S)"
fi

pubkey="$(printf '%s' "$PUBKEY_BASE64" | base64 -d)"
printf 'command="%s",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding %s\n' \
    "$DISPATCHER_PATH" "$pubkey" > "$authorized_keys"

chown "$RESTRICTED_USER:$RESTRICTED_USER" "$authorized_keys"
chmod 600 "$authorized_keys"

cat > "/etc/sudoers.d/${RESTRICTED_USER}-xray" <<EOF_SUDOERS
${RESTRICTED_USER} ALL=(root) NOPASSWD: /usr/local/sbin/xray-add-client, /usr/local/sbin/xray-remove-client, /usr/local/sbin/xray-build-vless-link, /usr/local/sbin/xray-list-clients
EOF_SUDOERS
chmod 440 "/etc/sudoers.d/${RESTRICTED_USER}-xray"
visudo -cf "/etc/sudoers.d/${RESTRICTED_USER}-xray" >/dev/null

echo "$RESTRICTED_USER готов"
EOF

printf 'Ограниченный пользователь установлен: %s@%s\n' "$RESTRICTED_USER" "$HOST"
printf 'Рекомендуемое изменение в Flask env: export VPN_SSH_USER=%q\n' "$RESTRICTED_USER"

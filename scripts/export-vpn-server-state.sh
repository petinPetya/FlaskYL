#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Использование:
  export-vpn-server-state.sh --host <host> --user <user> --key-path <path>
                             [--port <port>] [--ssh-config-file <path>]
                             [--output-dir <dir>] [--prefix <prefix>]

Экспортирует текущее состояние VPN-сервера в локальный каталог с отметкой
времени, а также создаёт tar.gz-архив и sha256-сумму.
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
USER=""
KEY_PATH=""
PORT="22"
SSH_CONFIG_FILE="/dev/null"
OUTPUT_DIR="backups"
PREFIX="vpn-server-state"

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
        --port)
            PORT="${2:-}"
            shift 2
            ;;
        --ssh-config-file)
            SSH_CONFIG_FILE="${2:-}"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --prefix)
            PREFIX="${2:-}"
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
[[ -n "$USER" ]] || fail "Нужно указать --user"
[[ -n "$KEY_PATH" ]] || fail "Нужно указать --key-path"
[[ -f "$KEY_PATH" ]] || fail "SSH-ключ не найден: $KEY_PATH"

require_command ssh
require_command scp
require_command tar
require_command sha256sum

timestamp="$(date +%Y%m%d-%H%M%S)"
safe_host="${HOST//[^A-Za-z0-9._-]/_}"
snapshot_name="${PREFIX}-${safe_host}-${timestamp}"
snapshot_dir="${OUTPUT_DIR}/${snapshot_name}"
archive_path="${OUTPUT_DIR}/${snapshot_name}.tar.gz"

mkdir -p "$snapshot_dir"

ssh_base=(
    ssh
    -F "$SSH_CONFIG_FILE"
    -i "$KEY_PATH"
    -p "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
    "${USER}@${HOST}"
)

scp_base=(
    scp
    -F "$SSH_CONFIG_FILE"
    -i "$KEY_PATH"
    -P "$PORT"
    -o BatchMode=yes
    -o ConnectTimeout=10
)

capture_remote_output() {
    local remote_command="$1"
    local output_file="$2"

    "${ssh_base[@]}" "bash -lc $(printf '%q' "$remote_command")" > "$output_file" 2>&1
}

copy_remote_file() {
    local remote_path="$1"
    local output_path="$2"

    if "${ssh_base[@]}" "test -f $(printf '%q' "$remote_path")"; then
        "${scp_base[@]}" "${USER}@${HOST}:${remote_path}" "$output_path"
    fi
}

copy_remote_file "/usr/local/etc/xray/config.json" "${snapshot_dir}/config.json"
copy_remote_file "/etc/lowlands-vpn/xray.env" "${snapshot_dir}/xray.env"
copy_remote_file \
    "/etc/sysctl.d/99-lowlands-vpn-disable-ipv6.conf" \
    "${snapshot_dir}/99-lowlands-vpn-disable-ipv6.conf"

capture_remote_output "uname -a" "${snapshot_dir}/uname.txt"
capture_remote_output "date --iso-8601=seconds" "${snapshot_dir}/captured_at.txt"
capture_remote_output "systemctl status xray --no-pager" "${snapshot_dir}/systemctl-xray.txt"
capture_remote_output "journalctl -u xray -n 200 --no-pager" "${snapshot_dir}/journal-xray.txt"
capture_remote_output "ip -4 addr show" "${snapshot_dir}/ip-4-addr.txt"
capture_remote_output "ip -6 addr show" "${snapshot_dir}/ip-6-addr.txt"
capture_remote_output "ip -4 route show" "${snapshot_dir}/ip-4-route.txt"
capture_remote_output "ip -6 route show" "${snapshot_dir}/ip-6-route.txt"
capture_remote_output "ss -ltnp" "${snapshot_dir}/ss-ltnp.txt"
capture_remote_output \
    "sysctl net.ipv6.conf.all.disable_ipv6 net.ipv6.conf.default.disable_ipv6 net.ipv6.conf.eth0.disable_ipv6 2>/dev/null || true" \
    "${snapshot_dir}/sysctl-ipv6.txt"
capture_remote_output "xray version 2>&1 || true" "${snapshot_dir}/xray-version.txt"
capture_remote_output \
    "/usr/local/sbin/xray-list-clients --json 2>&1 || true" \
    "${snapshot_dir}/xray-list-clients.json"

tar -C "$OUTPUT_DIR" -czf "$archive_path" "$snapshot_name"
sha256sum "$archive_path" > "${archive_path}.sha256"

printf 'Каталог снимка: %s\n' "$snapshot_dir"
printf 'Архив: %s\n' "$archive_path"
printf 'Контрольная сумма: %s.sha256\n' "$archive_path"

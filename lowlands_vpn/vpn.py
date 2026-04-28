import json
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote

from flask import current_app


class VpnProvisioningError(RuntimeError):
    pass


@dataclass
class RemoteCommandResult:
    payload: dict
    stdout: str
    stderr: str


def is_vpn_auto_provisioning_enabled() -> bool:
    return bool(
        current_app.config.get("VPN_AUTO_PROVISION", True)
        and current_app.config.get("VPN_SSH_HOST")
        and current_app.config.get("VPN_SSH_USER")
    )


def can_build_vless_link_locally() -> bool:
    required_values = (
        current_app.config.get("VLESS_HOST"),
        current_app.config.get("VLESS_PBK"),
        current_app.config.get("VLESS_SNI"),
        current_app.config.get("VLESS_SID"),
    )
    return all(required_values)


def ensure_device_vpn_identity(device) -> None:
    if not device.vpn_uuid:
        device.vpn_uuid = str(uuid.uuid4())
    if not device.vpn_email:
        device.vpn_email = f"device-{device.id}@xray"


def get_device_link_name(device) -> str:
    return device.name


def _can_build_vless_link_remotely() -> bool:
    return bool(
        is_vpn_auto_provisioning_enabled()
        and current_app.config.get("VPN_REMOTE_BUILD_LINK_SCRIPT")
    )


def _build_vless_link_locally(device) -> str:
    name = quote(get_device_link_name(device), safe="")
    return (
        f"vless://{quote(device.vpn_uuid, safe='')}@"
        f"{current_app.config['VLESS_HOST']}:"
        f"{current_app.config['VLESS_PORT']}"
        f"?type={quote(current_app.config['VLESS_TYPE'], safe='')}"
        f"&security={quote(current_app.config['VLESS_SECURITY'], safe='')}"
        f"&pbk={quote(current_app.config['VLESS_PBK'], safe='')}"
        f"&fp={quote(current_app.config['VLESS_FP'], safe='')}"
        f"&sni={quote(current_app.config['VLESS_SNI'], safe='')}"
        f"&sid={quote(current_app.config['VLESS_SID'], safe='')}"
        f"&flow={quote(current_app.config['VLESS_FLOW'], safe='')}"
        f"&encryption={quote(current_app.config['VLESS_ENCRYPTION'], safe='')}"
        f"#{name}"
    )


def _build_vless_link_remotely(device) -> str:
    result = run_remote_json_command(
        current_app.config["VPN_REMOTE_BUILD_LINK_SCRIPT"],
        "--uuid",
        device.vpn_uuid,
        "--name",
        get_device_link_name(device),
        "--json",
    )
    link = result.payload.get("link")
    if not link:
        raise VpnProvisioningError("VPN server did not return a VLESS link.")
    return link


def build_vless_link(device) -> str:
    if not device.vpn_uuid:
        raise VpnProvisioningError("У устройства еще нет VPN UUID.")

    if _can_build_vless_link_remotely():
        return _build_vless_link_remotely(device)

    if can_build_vless_link_locally():
        return _build_vless_link_locally(device)

    raise VpnProvisioningError(
        "Не удалось собрать VLESS-ссылку для устройства."
    )


def provision_device(device) -> None:
    if not is_vpn_auto_provisioning_enabled():
        return

    ensure_device_vpn_identity(device)
    add_result = None

    try:
        add_result = run_remote_json_command(
            current_app.config["VPN_REMOTE_ADD_SCRIPT"],
            "--email",
            device.vpn_email,
            "--uuid",
            device.vpn_uuid,
            "--name",
            get_device_link_name(device),
        )
    except VpnProvisioningError as error:
        if not _looks_like_existing_client_error(str(error)):
            raise

    link = None
    if add_result:
        link = add_result.payload.get("link")
    device.vpn_link = link or build_vless_link(device)


def revoke_device_on_server(device) -> None:
    if not is_vpn_auto_provisioning_enabled():
        return

    if device.vpn_uuid:
        run_remote_json_command(
            current_app.config["VPN_REMOTE_REMOVE_SCRIPT"],
            "--uuid",
            device.vpn_uuid,
        )
        return

    if device.vpn_email:
        run_remote_json_command(
            current_app.config["VPN_REMOTE_REMOVE_SCRIPT"],
            "--email",
            device.vpn_email,
        )


def remove_server_vless_client_by_uuid(client_uuid: str) -> dict:
    if not is_vpn_auto_provisioning_enabled():
        raise VpnProvisioningError("Автопровижининг VPN не настроен.")

    result = run_remote_json_command(
        current_app.config["VPN_REMOTE_REMOVE_SCRIPT"],
        "--uuid",
        client_uuid,
    )
    return result.payload


def update_server_vless_client_email(
    client_uuid: str, new_email: str, device_name: str | None = None
) -> dict:
    if not is_vpn_auto_provisioning_enabled():
        raise VpnProvisioningError("Автопровижининг VPN не настроен.")

    if not client_uuid:
        raise VpnProvisioningError(
            "Не передан UUID клиента для обновления email."
        )

    normalized_email = (new_email or "").strip()
    if not normalized_email:
        raise VpnProvisioningError("Не передан новый email клиента.")

    script_path = current_app.config.get("VPN_REMOTE_UPDATE_EMAIL_SCRIPT")
    if not script_path:
        raise VpnProvisioningError(
            "Не настроен путь до скрипта VPN_REMOTE_UPDATE_EMAIL_SCRIPT."
        )

    command_args = [
        "--uuid",
        client_uuid,
        "--email",
        normalized_email,
        "--json",
    ]
    if device_name:
        command_args.extend(["--name", device_name])

    result = run_remote_json_command(script_path, *command_args)
    return result.payload


def run_remote_json_command(
    script_path: str,
    *script_args: str,
    timeout_seconds: float | None = None,
    retry_attempts: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> RemoteCommandResult:
    result = run_remote_command(
        script_path,
        *script_args,
        timeout_seconds=timeout_seconds,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    payload = {}
    if result.stdout:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise VpnProvisioningError(
                f"VPN server returned invalid JSON: {result.stdout}"
            ) from error
    return RemoteCommandResult(
        payload=payload, stdout=result.stdout, stderr=result.stderr
    )


def run_remote_command(
    script_path: str,
    *script_args: str,
    timeout_seconds: float | None = None,
    retry_attempts: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> subprocess.CompletedProcess:
    ssh_command = [
        "ssh",
        "-F",
        current_app.config["VPN_SSH_CONFIG_FILE"],
        "-p",
        str(current_app.config["VPN_SSH_PORT"]),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={current_app.config['VPN_SSH_CONNECT_TIMEOUT']}",
        "-o",
        "LogLevel=ERROR",
    ]
    if current_app.config.get("VPN_SSH_KEY_PATH"):
        ssh_command.extend(["-i", current_app.config["VPN_SSH_KEY_PATH"]])
    if not current_app.config.get("VPN_SSH_STRICT_HOST_KEY_CHECKING", True):
        ssh_command.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]
        )

    ssh_command.append(
        f"{current_app.config['VPN_SSH_USER']}@"
        f"{current_app.config['VPN_SSH_HOST']}"
    )
    remote_command = " ".join(
        shlex.quote(part) for part in (script_path, *script_args) if part
    )
    command_timeout = max(
        float(
            timeout_seconds
            if timeout_seconds is not None
            else current_app.config.get("VPN_SSH_COMMAND_TIMEOUT", 20.0)
        ),
        1.0,
    )
    retries = max(
        int(
            retry_attempts
            if retry_attempts is not None
            else current_app.config.get("VPN_SSH_COMMAND_RETRIES", 1)
        ),
        0,
    )
    retry_backoff = max(
        float(
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else current_app.config.get("VPN_SSH_RETRY_BACKOFF_SECONDS", 0.75)
        ),
        0.0,
    )
    max_attempts = retries + 1
    last_error: VpnProvisioningError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            try:
                completed_process = subprocess.run(
                    [*ssh_command, remote_command],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=command_timeout,
                )
            except TypeError:
                # Test doubles can monkeypatch subprocess.run without timeout
                # kwarg.
                completed_process = subprocess.run(
                    [*ssh_command, remote_command],
                    capture_output=True,
                    check=False,
                    text=True,
                )
        except subprocess.TimeoutExpired as error:
            last_error = VpnProvisioningError(
                f"VPN SSH command timed out after {command_timeout:.1f}s: {
                    script_path
                }"
            )
            if attempt < max_attempts:
                _sleep_before_retry(attempt, retry_backoff)
                continue
            raise last_error from error
        except OSError as error:
            last_error = VpnProvisioningError(
                f"VPN SSH command failed to start: {error}"
            )
            if attempt < max_attempts and _is_retryable_ssh_error(str(error)):
                _sleep_before_retry(attempt, retry_backoff)
                continue
            raise last_error from error

        if completed_process.returncode == 0:
            return completed_process

        error_output = (
            completed_process.stderr.strip()
            or completed_process.stdout.strip()
        )
        last_error = VpnProvisioningError(
            error_output
            or f"VPN command failed with code {completed_process.returncode}"
        )

        if attempt < max_attempts and _is_retryable_ssh_error(error_output):
            _sleep_before_retry(attempt, retry_backoff)
            continue

        raise last_error

    if last_error is not None:
        raise last_error
    raise VpnProvisioningError("VPN SSH command failed for an unknown reason.")


def _sleep_before_retry(attempt: int, base_backoff: float) -> None:
    if base_backoff <= 0:
        return
    delay_seconds = min(base_backoff * attempt, 5.0)
    time.sleep(delay_seconds)


def _is_retryable_ssh_error(error_message: str) -> bool:
    if not error_message:
        return False

    message = error_message.strip().lower()
    retryable_fragments = (
        "timed out",
        "timeout",
        "connection refused",
        "connection reset",
        "connection closed",
        "broken pipe",
        "no route to host",
        "network is unreachable",
        "temporary failure in name resolution",
        "could not resolve hostname",
        "kex_exchange_identification",
        "connection unexpectedly closed",
        "banner exchange",
        "software caused connection abort",
        "resource temporarily unavailable",
    )
    return any(fragment in message for fragment in retryable_fragments)


def _looks_like_existing_client_error(error_message: str) -> bool:
    return (
        "UUID already exists" in error_message
        or "Email already exists" in error_message
    )


def list_server_vless_clients(
    *,
    timeout_seconds: float | None = None,
    retry_attempts: int | None = None,
    retry_backoff_seconds: float | None = None,
) -> dict:
    if not is_vpn_auto_provisioning_enabled():
        raise VpnProvisioningError("Автопровижининг VPN не настроен.")

    result = run_remote_json_command(
        current_app.config["VPN_REMOTE_LIST_SCRIPT"],
        "--json",
        timeout_seconds=timeout_seconds,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    return {
        "stats_enabled": bool(result.payload.get("stats_enabled", False)),
        "clients": result.payload.get("clients", []),
        "inbound_tag": result.payload.get("inbound_tag"),
        "config_path": result.payload.get("config_path"),
    }

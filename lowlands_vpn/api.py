from __future__ import annotations

from collections import deque
from functools import wraps
import os
from threading import Lock
import time

from flask import Blueprint, request
from flask_login import current_user, login_user, logout_user
from flask_wtf.csrf import generate_csrf

from lowlands_vpn.data import PLANS
from lowlands_vpn.email_verification import (
    is_email_verification_required,
)
from lowlands_vpn.extensions import db
from lowlands_vpn.models import (
    Device,
    Invoice,
    Subscription,
    Tariff,
    User,
    utc_now,
)
from lowlands_vpn.services import (
    SUBSCRIPTION_REQUEST_TYPE,
    create_subscription_request,
    delete_user_account,
    get_current_subscription,
    get_pending_subscription_request,
    sync_user_subscriptions,
)
from lowlands_vpn.vpn import (
    VpnProvisioningError,
    is_vpn_auto_provisioning_enabled,
    list_server_vless_clients,
    provision_device,
    revoke_device_on_server,
)

api_bp = Blueprint("api", __name__, url_prefix="/api")

LIVE_METRICS_CACHE_SECONDS = 4.0
LIVE_METRICS_HISTORY_LIMIT = 90
ADMIN_VPN_LIST_TIMEOUT_SECONDS = 8.0
ADMIN_VPN_LIST_RETRY_ATTEMPTS = 0
ADMIN_VPN_FAILURE_BACKOFF_SECONDS = 60.0
LIVE_METRICS_LOCK = Lock()
LIVE_METRICS_STATE = {
    "cache_until": 0.0,
    "cached_payload": None,
    "last_cpu_sample": None,
    "last_cpu_percent": None,
    "last_clients_totals": {},
    "last_clients_ts": None,
    "throughput_history": deque(maxlen=LIVE_METRICS_HISTORY_LIMIT),
    "online_history": deque(maxlen=LIVE_METRICS_HISTORY_LIMIT),
    "vpn_cached_metrics": None,
    "vpn_backoff_until": 0.0,
}


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_cpu_sample() -> tuple[int, int] | None:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as file:
            first_line = file.readline().strip()
    except OSError:
        return None

    parts = first_line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None

    values = []
    for item in parts[1:]:
        try:
            values.append(int(item))
        except ValueError:
            return None

    total = sum(values)
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return total, idle


def _read_meminfo() -> dict[str, int]:
    meminfo: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as file:
            for line in file:
                key, _, raw_value = line.partition(":")
                if not key:
                    continue
                number = raw_value.strip().split(" ", 1)[0]
                try:
                    meminfo[key] = int(number)
                except ValueError:
                    continue
    except OSError:
        return {}
    return meminfo


def _collect_host_metrics() -> dict:
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except OSError:
        load_1m, load_5m, load_15m = 0.0, 0.0, 0.0

    cpu_sample = _read_cpu_sample()
    cpu_percent = None

    with LIVE_METRICS_LOCK:
        previous_sample = LIVE_METRICS_STATE["last_cpu_sample"]
        previous_percent = LIVE_METRICS_STATE["last_cpu_percent"]
        if cpu_sample is not None and previous_sample is not None:
            total_delta = cpu_sample[0] - previous_sample[0]
            idle_delta = cpu_sample[1] - previous_sample[1]
            if total_delta > 0:
                usage = 100.0 * (1.0 - (idle_delta / total_delta))
                cpu_percent = max(0.0, min(100.0, usage))
            else:
                cpu_percent = previous_percent
        elif previous_percent is not None:
            cpu_percent = previous_percent

        if cpu_sample is not None:
            LIVE_METRICS_STATE["last_cpu_sample"] = cpu_sample
        LIVE_METRICS_STATE["last_cpu_percent"] = cpu_percent

    meminfo = _read_meminfo()
    mem_total_bytes = _safe_int(meminfo.get("MemTotal")) * 1024
    mem_available_bytes = _safe_int(meminfo.get("MemAvailable")) * 1024
    mem_used_bytes = max(mem_total_bytes - mem_available_bytes, 0)
    mem_used_percent = (
        round((mem_used_bytes / mem_total_bytes) * 100, 2)
        if mem_total_bytes
        else None
    )

    disk_total_bytes = None
    disk_free_bytes = None
    disk_used_bytes = None
    disk_used_percent = None
    try:
        disk_stat = os.statvfs("/")
    except OSError:
        pass
    else:
        disk_total_bytes = disk_stat.f_blocks * disk_stat.f_frsize
        disk_free_bytes = disk_stat.f_bavail * disk_stat.f_frsize
        disk_used_bytes = max(disk_total_bytes - disk_free_bytes, 0)
        if disk_total_bytes > 0:
            disk_used_percent = round(
                (disk_used_bytes / disk_total_bytes) * 100, 2
            )

    return {
        "load_avg": {
            "1m": round(load_1m, 2),
            "5m": round(load_5m, 2),
            "15m": round(load_15m, 2),
        },
        "cpu_percent": round(cpu_percent, 2)
        if cpu_percent is not None
        else None,
        "memory": {
            "total_bytes": mem_total_bytes,
            "used_bytes": mem_used_bytes,
            "available_bytes": mem_available_bytes,
            "used_percent": mem_used_percent,
        },
        "disk_root": {
            "total_bytes": disk_total_bytes,
            "used_bytes": disk_used_bytes,
            "free_bytes": disk_free_bytes,
            "used_percent": disk_used_percent,
        },
    }


def _collect_vpn_live_metrics(now_epoch: int) -> dict:
    vpn_metrics = {
        "auto_provisioning_enabled": is_vpn_auto_provisioning_enabled(),
        "stats_enabled": False,
        "clients_total": 0,
        "clients_with_stats": 0,
        "traffic_total_bytes": 0,
        "traffic_uplink_bytes": 0,
        "traffic_downlink_bytes": 0,
        "online_clients_estimated": 0,
        "throughput_bps": 0.0,
        "estimation_ready": False,
        "throughput_history": [],
        "online_history": [],
    }

    if not vpn_metrics["auto_provisioning_enabled"]:
        with LIVE_METRICS_LOCK:
            vpn_metrics["throughput_history"] = list(
                LIVE_METRICS_STATE["throughput_history"]
            )
            vpn_metrics["online_history"] = list(
                LIVE_METRICS_STATE["online_history"]
            )
        return vpn_metrics

    now_monotonic = time.monotonic()
    with LIVE_METRICS_LOCK:
        cached_vpn_metrics = LIVE_METRICS_STATE.get("vpn_cached_metrics")
        backoff_until = float(LIVE_METRICS_STATE.get("vpn_backoff_until", 0.0))
        if cached_vpn_metrics and now_monotonic < backoff_until:
            cached_payload = dict(cached_vpn_metrics)
            cached_payload["stale"] = True
            cached_payload["warning"] = (
                "VPN временно недоступен, показаны последние данные."
            )
            cached_payload["throughput_history"] = list(
                LIVE_METRICS_STATE["throughput_history"]
            )
            cached_payload["online_history"] = list(
                LIVE_METRICS_STATE["online_history"]
            )
            return cached_payload

    try:
        server_payload = list_server_vless_clients(
            timeout_seconds=ADMIN_VPN_LIST_TIMEOUT_SECONDS,
            retry_attempts=ADMIN_VPN_LIST_RETRY_ATTEMPTS,
            retry_backoff_seconds=0.0,
        )
    except VpnProvisioningError as error:
        with LIVE_METRICS_LOCK:
            cached_vpn_metrics = LIVE_METRICS_STATE.get("vpn_cached_metrics")
            LIVE_METRICS_STATE["vpn_backoff_until"] = (
                now_monotonic + ADMIN_VPN_FAILURE_BACKOFF_SECONDS
            )
            if cached_vpn_metrics:
                cached_payload = dict(cached_vpn_metrics)
                cached_payload["stale"] = True
                cached_payload["warning"] = (
                    "VPN временно недоступен, показаны последние данные."
                )
                cached_payload["throughput_history"] = list(
                    LIVE_METRICS_STATE["throughput_history"]
                )
                cached_payload["online_history"] = list(
                    LIVE_METRICS_STATE["online_history"]
                )
                return cached_payload

            vpn_metrics["error"] = str(error)
            vpn_metrics["throughput_history"] = list(
                LIVE_METRICS_STATE["throughput_history"]
            )
            vpn_metrics["online_history"] = list(
                LIVE_METRICS_STATE["online_history"]
            )
        return vpn_metrics

    clients = server_payload.get("clients", [])
    vpn_metrics["stats_enabled"] = bool(server_payload.get("stats_enabled"))
    vpn_metrics["clients_total"] = len(clients)
    vpn_metrics["inbound_tag"] = server_payload.get("inbound_tag")

    clients_totals: dict[str, int] = {}

    for client in clients:
        stats = client.get("stats") or {}
        if not stats.get("available"):
            continue

        total_bytes = _safe_int(stats.get("total_bytes"))
        uplink_bytes = _safe_int(stats.get("uplink_bytes"))
        downlink_bytes = _safe_int(stats.get("downlink_bytes"))
        vpn_metrics["clients_with_stats"] += 1
        vpn_metrics["traffic_total_bytes"] += total_bytes
        vpn_metrics["traffic_uplink_bytes"] += uplink_bytes
        vpn_metrics["traffic_downlink_bytes"] += downlink_bytes

        identity = client.get("uuid") or client.get("email")
        if identity:
            clients_totals[str(identity)] = total_bytes

    with LIVE_METRICS_LOCK:
        previous_totals = LIVE_METRICS_STATE["last_clients_totals"]
        previous_ts = LIVE_METRICS_STATE["last_clients_ts"]

        delta_seconds = (
            now_monotonic - previous_ts
            if isinstance(previous_ts, (int, float))
            else 0.0
        )
        delta_total = 0
        online_estimated = 0

        if delta_seconds > 0 and previous_totals and clients_totals:
            for identity, current_total in clients_totals.items():
                previous_total = previous_totals.get(identity)
                if previous_total is None:
                    continue
                growth = current_total - previous_total
                if growth > 0:
                    delta_total += growth
                    online_estimated += 1
            vpn_metrics["estimation_ready"] = True
        else:
            vpn_metrics["estimation_ready"] = False

        throughput_bps = (
            (delta_total / delta_seconds) if delta_seconds > 0 else 0.0
        )
        vpn_metrics["throughput_bps"] = round(throughput_bps, 2)
        vpn_metrics["online_clients_estimated"] = online_estimated

        LIVE_METRICS_STATE["last_clients_totals"] = clients_totals
        LIVE_METRICS_STATE["last_clients_ts"] = now_monotonic
        LIVE_METRICS_STATE["throughput_history"].append(
            {"ts": now_epoch, "value": vpn_metrics["throughput_bps"]}
        )
        LIVE_METRICS_STATE["online_history"].append(
            {"ts": now_epoch, "value": vpn_metrics["online_clients_estimated"]}
        )
        LIVE_METRICS_STATE["vpn_cached_metrics"] = dict(vpn_metrics)
        LIVE_METRICS_STATE["vpn_backoff_until"] = 0.0

        vpn_metrics["throughput_history"] = list(
            LIVE_METRICS_STATE["throughput_history"]
        )
        vpn_metrics["online_history"] = list(
            LIVE_METRICS_STATE["online_history"]
        )

    return vpn_metrics


def _build_admin_live_dashboard_payload() -> dict:
    processed_statuses = ("approved", "paid")
    now = utc_now()
    stats = {
        "users_total": User.query.count(),
        "admins_total": User.query.filter_by(is_admin=True).count(),
        "subscriptions_total": Subscription.query.count(),
        "subscriptions_active": Subscription.query.filter_by(
            status="active"
        ).count(),
        "invoices_total": Invoice.query.count(),
        "invoices_approved": Invoice.query.filter(
            Invoice.status.in_(processed_statuses)
        ).count(),
        "invoices_paid": Invoice.query.filter_by(status="paid").count(),
        "requests_pending": Invoice.query.filter_by(
            type=SUBSCRIPTION_REQUEST_TYPE, status="pending"
        ).count(),
        "devices_total": Device.query.count(),
        "devices_ready": Device.query.filter_by(
            provisioning_state="ready"
        ).count(),
    }

    now_epoch = int(now.timestamp())
    return {
        "timestamp": serialize_datetime(now),
        "stats": stats,
        "host": _collect_host_metrics(),
        "vpn": _collect_vpn_live_metrics(now_epoch),
    }


def json_success(data=None, status: int = 200, message: str | None = None):
    payload = {"ok": True}
    if message:
        payload["message"] = message
    payload["data"] = data
    return payload, status


def json_error(message: str, status: int = 400, details=None):
    payload = {"ok": False, "error": message}
    if details is not None:
        payload["details"] = details
    return payload, status


def api_login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return json_error("Требуется авторизация.", 401)
        if not current_user.is_active:
            return json_error("Аккаунт деактивирован.", 403)
        return view_func(*args, **kwargs)

    return wrapped


def api_admin_required(view_func):
    @wraps(view_func)
    @api_login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            return json_error("Требуются права администратора.", 403)
        return view_func(*args, **kwargs)

    return wrapped


def user_email_is_verified(user: User) -> bool:
    return bool(user.email_verified_at)


def api_email_verification_required(view_func):
    @wraps(view_func)
    @api_login_required
    def wrapped(*args, **kwargs):
        if not is_email_verification_required():
            return view_func(*args, **kwargs)
        if user_email_is_verified(current_user):
            return view_func(*args, **kwargs)
        return json_error(
            "Подтвердите email перед выполнением этого действия.",
            403,
        )

    return wrapped


def serialize_datetime(value) -> str | None:
    return value.isoformat() if value else None


def serialize_tariff(tariff: Tariff) -> dict:
    features_by_name = {
        plan["name"]: plan.get("features", []) for plan in PLANS
    }
    return {
        "id": tariff.id,
        "name": tariff.name,
        "description": tariff.description,
        "price_cents": tariff.price_cents,
        "price_rub": f"{tariff.price_cents / 100:.2f}",
        "days_valid": tariff.days_valid,
        "device_limit": tariff.device_limit,
        "traffic_limit_bytes": tariff.traffic_limit_bytes,
        "is_unlimited_traffic": tariff.is_unlimited_traffic(),
        "is_unlimited_time": tariff.is_unlimited_time(),
        "is_active": tariff.is_active,
        "is_popular": tariff.is_popular,
        "sort_order": tariff.sort_order,
        "created_at": serialize_datetime(tariff.created_at),
        "features": features_by_name.get(tariff.name, []),
    }


def serialize_subscription(subscription: Subscription | None) -> dict | None:
    if subscription is None:
        return None

    return {
        "id": subscription.id,
        "user_id": subscription.user_id,
        "tariff_id": subscription.tariff_id,
        "status": subscription.status,
        "starts_at": serialize_datetime(subscription.starts_at),
        "expires_at": serialize_datetime(subscription.expires_at),
        "expires_at_display": subscription.get_expires_at_display(),
        "is_lifetime": subscription.is_lifetime,
        "is_active": subscription.is_active(),
        "used_traffic_bytes": subscription.used_traffic_bytes,
        "traffic_limit_bytes": subscription.traffic_limit_bytes,
        "remaining_traffic_bytes": subscription.get_remaining_traffic(),
        "remaining_days": subscription.get_remaining_days(),
        "usage_percent": subscription.get_usage_percent(),
        "device_limit": subscription.get_device_limit(),
        "active_device_count": subscription.get_active_device_count(),
        "available_device_slots": subscription.get_available_device_slots(),
        "created_at": serialize_datetime(subscription.created_at),
        "updated_at": serialize_datetime(subscription.updated_at),
        "tariff": serialize_tariff(subscription.tariff)
        if subscription.tariff
        else None,
    }


def serialize_device(device: Device) -> dict:
    return {
        "id": device.id,
        "subscription_id": device.subscription_id,
        "name": device.name,
        "platform": device.platform,
        "status": device.status,
        "provisioning_state": device.provisioning_state,
        "vpn_uuid": device.vpn_uuid,
        "vpn_email": device.vpn_email,
        "vpn_link": device.vpn_link,
        "assigned_ip": device.assigned_ip,
        "last_error": device.last_error,
        "created_at": serialize_datetime(device.created_at),
        "updated_at": serialize_datetime(device.updated_at),
        "provisioned_at": serialize_datetime(device.provisioned_at),
        "revoked_at": serialize_datetime(device.revoked_at),
    }


def serialize_invoice(invoice: Invoice) -> dict:
    tariff = None
    tariff_id = invoice.get_requested_tariff_id()
    if tariff_id:
        tariff = db.session.get(Tariff, tariff_id)

    return {
        "id": invoice.id,
        "user_id": invoice.user_id,
        "subscription_id": invoice.subscription_id,
        "amount_cents": invoice.amount_cents,
        "amount_rub": f"{invoice.amount_cents / 100:.2f}",
        "status": invoice.status,
        "type": invoice.type,
        "review_channel": invoice.review_channel,
        "review_reference": invoice.review_reference,
        "external_url": invoice.external_url,
        "payment_system": invoice.payment_system,
        "payment_system_id": invoice.payment_system_id,
        "payment_url": invoice.payment_url,
        "description": invoice.description,
        "metadata": invoice.get_metadata(),
        "created_at": serialize_datetime(invoice.created_at),
        "processed_at": serialize_datetime(invoice.processed_at),
        "paid_at": serialize_datetime(invoice.paid_at),
        "tariff": serialize_tariff(tariff) if tariff else None,
    }


def serialize_user(user: User, include_relations: bool = False) -> dict:
    payload = {
        "id": user.id,
        "email": user.email,
        "balance_cents": user.balance,
        "balance_rub": f"{user.balance / 100:.2f}",
        "is_active": user.is_active,
        "is_admin": user.is_admin,
        "is_email_verified": user_email_is_verified(user),
        "email_verified_at": serialize_datetime(user.email_verified_at),
        "created_at": serialize_datetime(user.created_at),
        "last_login_at": serialize_datetime(user.last_login_at),
        "updated_at": serialize_datetime(user.updated_at),
    }
    if include_relations:
        payload["subscriptions_count"] = user.subscriptions.count()
        payload["invoices_count"] = user.invoices.count()
        payload["devices_count"] = (
            Device.query.join(Subscription)
            .filter(Subscription.user_id == user.id)
            .count()
        )
    return payload


def get_json_payload() -> dict:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


@api_bp.get("")
def api_index():
    return json_success(
        {
            "service": "Lowlands VPN API",
            "version": 1,
            "session_auth": True,
            "endpoints": [
                "/api",
                "/api/tariffs",
                "/api/auth/csrf",
                "/api/auth/login",
                "/api/auth/logout",
                "/api/auth/me",
                "/api/invoices",
                "/api/subscriptions/current",
                "/api/subscriptions/request",
                "/api/devices",
                "/api/devices/<device_id>/revoke",
                "/api/admin/overview",
                "/api/admin/live-dashboard",
                "/api/admin/users",
                "/api/admin/users/<user_id>/verify-email",
                "/api/admin/users/<user_id>/delete",
                "/api/admin/server-vless-clients",
            ],
        }
    )


@api_bp.get("/tariffs")
def api_tariffs():
    tariffs = (
        Tariff.query.filter_by(is_active=True)
        .order_by(Tariff.sort_order.asc(), Tariff.name.asc())
        .all()
    )
    return json_success(
        {"tariffs": [serialize_tariff(tariff) for tariff in tariffs]}
    )


@api_bp.post("/auth/login")
def api_login():
    payload = get_json_payload()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    if not email or not password:
        return json_error("Нужны email и пароль.", 400)

    user = db.session.scalar(db.select(User).where(User.email == email))
    if user is None or not user.check_password(password):
        return json_error("Неверный email или пароль.", 401)
    if not user.is_active:
        return json_error("Аккаунт деактивирован.", 403)

    user.last_login_at = utc_now()
    db.session.commit()
    login_user(user)
    return json_success(
        {"user": serialize_user(user)}, message="Авторизация выполнена."
    )


@api_bp.get("/auth/csrf")
def api_csrf_token():
    return json_success(
        {"csrf_token": generate_csrf()},
        message="CSRF token issued.",
    )


@api_bp.post("/auth/logout")
@api_login_required
def api_logout():
    logout_user()
    return json_success(message="Сессия завершена.")


@api_bp.get("/auth/me")
@api_login_required
def api_me():
    current_subscription = get_current_subscription(current_user)
    pending_request = get_pending_subscription_request(current_user)
    return json_success(
        {
            "user": serialize_user(current_user),
            "current_subscription": serialize_subscription(
                current_subscription
            ),
            "pending_request": serialize_invoice(pending_request)
            if pending_request
            else None,
        }
    )


@api_bp.get("/invoices")
@api_login_required
def api_invoices():
    invoices = current_user.invoices.order_by(Invoice.created_at.desc()).all()
    return json_success(
        {"invoices": [serialize_invoice(invoice) for invoice in invoices]}
    )


@api_bp.get("/subscriptions/current")
@api_login_required
def api_current_subscription():
    return json_success(
        {
            "subscription": serialize_subscription(
                get_current_subscription(current_user)
            )
        }
    )


@api_bp.post("/subscriptions/request")
@api_email_verification_required
def api_request_subscription():
    payload = get_json_payload()
    tariff_id = payload.get("tariff_id")
    tariff_name = (payload.get("tariff_name") or "").strip()

    pending_request = get_pending_subscription_request(current_user)
    if pending_request is not None:
        return json_error(
            "У пользователя уже есть необработанная заявка на тариф.",
            409,
            {"invoice": serialize_invoice(pending_request)},
        )

    tariff = None
    if tariff_id:
        tariff = db.session.get(Tariff, tariff_id)
    elif tariff_name:
        tariff = db.session.scalar(
            db.select(Tariff).where(Tariff.name == tariff_name)
        )

    if tariff is None or not tariff.is_active:
        return json_error("Выбранный тариф недоступен.", 404)

    invoice = create_subscription_request(current_user, tariff)
    db.session.commit()
    return json_success(
        {"invoice": serialize_invoice(invoice)},
        status=201,
        message="Заявка на тариф создана.",
    )


@api_bp.get("/devices")
@api_login_required
def api_devices():
    devices = (
        Device.query.join(Subscription)
        .filter(Subscription.user_id == current_user.id)
        .order_by(Device.created_at.desc())
        .all()
    )
    return json_success(
        {"devices": [serialize_device(device) for device in devices]}
    )


@api_bp.post("/devices")
@api_email_verification_required
def api_create_device():
    payload = get_json_payload()
    name = (payload.get("name") or "").strip()
    platform = (payload.get("platform") or "").strip().lower()
    allowed_platforms = {"windows", "macos", "linux", "ios", "android"}

    if not name:
        return json_error("Нужно указать название устройства.", 400)
    if platform not in allowed_platforms:
        return json_error("Указана неподдерживаемая платформа.", 400)

    current_subscription = get_current_subscription(current_user)
    is_admin_unlimited = current_user.is_admin

    if current_subscription is None or not current_subscription.is_active():
        return json_error(
            "Добавлять устройства можно только к активной подписке.", 403
        )

    if not is_admin_unlimited and not current_subscription.can_add_device():
        return json_error("Лимит устройств по тарифу исчерпан.", 409)

    device = Device(
        subscription_id=current_subscription.id,
        name=name,
        platform=platform,
        status="pending",
        provisioning_state="requested",
    )
    db.session.add(device)
    db.session.flush()

    status = 201
    message = "Устройство создано."
    warning = None

    if is_vpn_auto_provisioning_enabled():
        try:
            provision_device(device)
        except VpnProvisioningError as error:
            device.mark_failed(str(error))
            status = 202
            message = "Устройство создано, но VPN не был выдан автоматически."
            warning = str(error)
        else:
            device.mark_ready()
            message = "Устройство создано, VPN-ссылка готова."
    else:
        message = (
            "Устройство создано. Автовыдача VPN выключена, "
            "поэтому устройство ждёт обработки."
        )

    db.session.commit()
    return json_success(
        {"device": serialize_device(device), "warning": warning},
        status=status,
        message=message,
    )


@api_bp.post("/devices/<string:device_id>/revoke")
@api_email_verification_required
def api_revoke_device(device_id: str):
    device = (
        Device.query.join(Subscription)
        .filter(
            Device.id == device_id, Subscription.user_id == current_user.id
        )
        .first()
    )
    if device is None:
        return json_error("Устройство не найдено.", 404)

    if device.status == "revoked":
        return json_success(
            {"device": serialize_device(device)},
            message="Устройство уже было отозвано ранее.",
        )

    try:
        revoke_device_on_server(device)
    except VpnProvisioningError as error:
        device.record_provisioning_error(str(error))
        db.session.commit()
        return json_error(
            "Не удалось отозвать VPN-конфиг устройства.",
            502,
            {"device": serialize_device(device)},
        )

    device.mark_revoked()
    db.session.commit()
    return json_success(
        {"device": serialize_device(device)}, message="Устройство отозвано."
    )


@api_bp.get("/admin/overview")
@api_admin_required
def api_admin_overview():
    for user in User.query.all():
        sync_user_subscriptions(user)

    processed_statuses = ("approved", "paid")
    stats = {
        "users_total": User.query.count(),
        "admins_total": User.query.filter_by(is_admin=True).count(),
        "subscriptions_total": Subscription.query.count(),
        "subscriptions_active": Subscription.query.filter_by(
            status="active"
        ).count(),
        "invoices_total": Invoice.query.count(),
        "invoices_approved": Invoice.query.filter(
            Invoice.status.in_(processed_statuses)
        ).count(),
        "invoices_paid": Invoice.query.filter_by(status="paid").count(),
        "requests_pending": Invoice.query.filter_by(
            type=SUBSCRIPTION_REQUEST_TYPE, status="pending"
        ).count(),
        "devices_total": Device.query.count(),
        "devices_ready": Device.query.filter_by(
            provisioning_state="ready"
        ).count(),
    }

    vpn_summary = {
        "auto_provisioning_enabled": is_vpn_auto_provisioning_enabled(),
        "server_clients_count": 0,
        "stats_enabled": False,
    }

    if is_vpn_auto_provisioning_enabled():
        try:
            server_payload = list_server_vless_clients(
                timeout_seconds=ADMIN_VPN_LIST_TIMEOUT_SECONDS,
                retry_attempts=ADMIN_VPN_LIST_RETRY_ATTEMPTS,
                retry_backoff_seconds=0.0,
            )
        except VpnProvisioningError as error:
            vpn_summary["error"] = str(error)
        else:
            vpn_summary["server_clients_count"] = len(
                server_payload["clients"]
            )
            vpn_summary["stats_enabled"] = bool(
                server_payload["stats_enabled"]
            )
            vpn_summary["inbound_tag"] = server_payload["inbound_tag"]

    return json_success({"stats": stats, "vpn": vpn_summary})


@api_bp.get("/admin/live-dashboard")
@api_admin_required
def api_admin_live_dashboard():
    now_monotonic = time.monotonic()
    with LIVE_METRICS_LOCK:
        if (
            LIVE_METRICS_STATE["cached_payload"] is not None
            and now_monotonic < LIVE_METRICS_STATE["cache_until"]
        ):
            return json_success(LIVE_METRICS_STATE["cached_payload"])

    payload = _build_admin_live_dashboard_payload()

    with LIVE_METRICS_LOCK:
        LIVE_METRICS_STATE["cached_payload"] = payload
        LIVE_METRICS_STATE["cache_until"] = (
            time.monotonic() + LIVE_METRICS_CACHE_SECONDS
        )

    return json_success(payload)


@api_bp.get("/admin/users")
@api_admin_required
def api_admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return json_success(
        {
            "users": [
                serialize_user(user, include_relations=True) for user in users
            ]
        }
    )


@api_bp.post("/admin/users/<string:user_id>/delete")
@api_admin_required
def api_admin_delete_user(user_id: str):
    user = db.session.get(User, user_id)
    if user is None:
        return json_error("Пользователь не найден.", 404)
    if user.id == current_user.id:
        return json_error(
            "Нельзя удалить собственный аккаунт администратора.", 409
        )

    try:
        summary = delete_user_account(user)
    except VpnProvisioningError as error:
        return json_error(
            "Не удалось удалить пользователя.",
            502,
            {"details": str(error)},
        )

    return json_success(
        {"summary": summary},
        message="Пользователь удален безвозвратно.",
    )


@api_bp.post("/admin/users/<string:user_id>/verify-email")
@api_admin_required
def api_admin_verify_user_email(user_id: str):
    user = db.session.get(User, user_id)
    if user is None:
        return json_error("Пользователь не найден.", 404)

    if user_email_is_verified(user):
        return json_success(
            {"user": serialize_user(user)},
            message="Email пользователя уже подтвержден.",
        )

    user.mark_email_verified()
    db.session.commit()
    return json_success(
        {"user": serialize_user(user)},
        message="Email пользователя подтвержден вручную.",
    )


@api_bp.get("/admin/server-vless-clients")
@api_admin_required
def api_admin_server_vless_clients():
    if not is_vpn_auto_provisioning_enabled():
        return json_error("VPN SSH-интеграция не настроена.", 503)

    server_payload = list_server_vless_clients(
        timeout_seconds=ADMIN_VPN_LIST_TIMEOUT_SECONDS,
        retry_attempts=ADMIN_VPN_LIST_RETRY_ATTEMPTS,
        retry_backoff_seconds=0.0,
    )
    uuids = [
        client.get("uuid")
        for client in server_payload["clients"]
        if client.get("uuid")
    ]
    device_by_uuid = {}
    if uuids:
        linked_devices = (
            Device.query.join(Subscription)
            .filter(Device.vpn_uuid.in_(uuids))
            .all()
        )
        device_by_uuid = {
            device.vpn_uuid: device
            for device in linked_devices
            if device.vpn_uuid
        }

    clients = []
    for client in server_payload["clients"]:
        linked_device = device_by_uuid.get(client.get("uuid"))
        clients.append(
            {
                **client,
                "device": serialize_device(linked_device)
                if linked_device
                else None,
                "user": serialize_user(linked_device.subscription.user)
                if linked_device
                else None,
            }
        )

    return json_success(
        {
            "stats_enabled": bool(server_payload["stats_enabled"]),
            "inbound_tag": server_payload["inbound_tag"],
            "config_path": server_payload["config_path"],
            "clients": clients,
        }
    )

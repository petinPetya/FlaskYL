from __future__ import annotations

from functools import wraps

from flask import Blueprint, request
from flask_login import current_user, login_user, logout_user

from lowlands_vpn.data import PLANS
from lowlands_vpn.extensions import db
from lowlands_vpn.models import Device, Invoice, Subscription, Tariff, User, utc_now
from lowlands_vpn.services import (
    SUBSCRIPTION_REQUEST_TYPE,
    create_subscription_request,
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


def serialize_datetime(value) -> str | None:
    return value.isoformat() if value else None


def serialize_tariff(tariff: Tariff) -> dict:
    features_by_name = {plan["name"]: plan.get("features", []) for plan in PLANS}
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
        "tariff": serialize_tariff(subscription.tariff) if subscription.tariff else None,
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
        "description": invoice.description,
        "metadata": invoice.get_metadata(),
        "created_at": serialize_datetime(invoice.created_at),
        "processed_at": serialize_datetime(invoice.processed_at),
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
                "/api/auth/login",
                "/api/auth/logout",
                "/api/auth/me",
                "/api/invoices",
                "/api/subscriptions/current",
                "/api/subscriptions/request",
                "/api/devices",
                "/api/devices/<device_id>/revoke",
                "/api/admin/overview",
                "/api/admin/users",
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
    return json_success({"tariffs": [serialize_tariff(tariff) for tariff in tariffs]})


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
    return json_success({"user": serialize_user(user)}, message="Авторизация выполнена.")


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
            "current_subscription": serialize_subscription(current_subscription),
            "pending_request": serialize_invoice(pending_request) if pending_request else None,
        }
    )


@api_bp.get("/invoices")
@api_login_required
def api_invoices():
    invoices = current_user.invoices.order_by(Invoice.created_at.desc()).all()
    return json_success({"invoices": [serialize_invoice(invoice) for invoice in invoices]})


@api_bp.get("/subscriptions/current")
@api_login_required
def api_current_subscription():
    return json_success(
        {"subscription": serialize_subscription(get_current_subscription(current_user))}
    )


@api_bp.post("/subscriptions/request")
@api_login_required
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
        tariff = db.session.scalar(db.select(Tariff).where(Tariff.name == tariff_name))

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
    return json_success({"devices": [serialize_device(device) for device in devices]})


@api_bp.post("/devices")
@api_login_required
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
        return json_error("Добавлять устройства можно только к активной подписке.", 403)

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
            "Устройство создано. Автовыдача VPN выключена, поэтому устройство ждёт обработки."
        )

    db.session.commit()
    return json_success(
        {"device": serialize_device(device), "warning": warning},
        status=status,
        message=message,
    )


@api_bp.post("/devices/<string:device_id>/revoke")
@api_login_required
def api_revoke_device(device_id: str):
    device = (
        Device.query.join(Subscription)
        .filter(Device.id == device_id, Subscription.user_id == current_user.id)
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
    return json_success({"device": serialize_device(device)}, message="Устройство отозвано.")


@api_bp.get("/admin/overview")
@api_admin_required
def api_admin_overview():
    for user in User.query.all():
        sync_user_subscriptions(user)

    stats = {
        "users_total": User.query.count(),
        "admins_total": User.query.filter_by(is_admin=True).count(),
        "subscriptions_total": Subscription.query.count(),
        "subscriptions_active": Subscription.query.filter_by(status="active").count(),
        "invoices_total": Invoice.query.count(),
            "invoices_approved": Invoice.query.filter_by(status="approved").count(),
        "requests_pending": Invoice.query.filter_by(
            type=SUBSCRIPTION_REQUEST_TYPE, status="pending"
        ).count(),
        "devices_total": Device.query.count(),
        "devices_ready": Device.query.filter_by(provisioning_state="ready").count(),
    }

    vpn_summary = {
        "auto_provisioning_enabled": is_vpn_auto_provisioning_enabled(),
        "server_clients_count": 0,
        "stats_enabled": False,
    }

    if is_vpn_auto_provisioning_enabled():
        try:
            server_payload = list_server_vless_clients()
        except VpnProvisioningError as error:
            vpn_summary["error"] = str(error)
        else:
            vpn_summary["server_clients_count"] = len(server_payload["clients"])
            vpn_summary["stats_enabled"] = bool(server_payload["stats_enabled"])
            vpn_summary["inbound_tag"] = server_payload["inbound_tag"]

    return json_success({"stats": stats, "vpn": vpn_summary})


@api_bp.get("/admin/users")
@api_admin_required
def api_admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return json_success(
        {"users": [serialize_user(user, include_relations=True) for user in users]}
    )


@api_bp.get("/admin/server-vless-clients")
@api_admin_required
def api_admin_server_vless_clients():
    if not is_vpn_auto_provisioning_enabled():
        return json_error("VPN SSH-интеграция не настроена.", 503)

    server_payload = list_server_vless_clients()
    uuids = [client.get("uuid") for client in server_payload["clients"] if client.get("uuid")]
    device_by_uuid = {}
    if uuids:
        linked_devices = (
            Device.query.join(Subscription)
            .filter(Device.vpn_uuid.in_(uuids))
            .all()
        )
        device_by_uuid = {
            device.vpn_uuid: device for device in linked_devices if device.vpn_uuid
        }

    clients = []
    for client in server_payload["clients"]:
        linked_device = device_by_uuid.get(client.get("uuid"))
        clients.append(
            {
                **client,
                "device": serialize_device(linked_device) if linked_device else None,
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

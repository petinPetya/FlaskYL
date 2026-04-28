from datetime import timedelta

from lowlands_vpn.extensions import db
from lowlands_vpn.models import (
    Device,
    Invoice,
    Subscription,
    Tariff,
    User,
    utc_now,
)

SUBSCRIPTION_REQUEST_TYPE = "subscription_request"


def get_subscription_revoke_reason(subscription: Subscription) -> str:
    if subscription.status == "expired":
        return "Подписка истекла. Устройство автоматически отозвано."
    if subscription.status == "traffic_exceeded":
        return "Лимит трафика исчерпан. Устройство автоматически отозвано."
    if subscription.status == "revoked":
        return "Подписка отозвана. Устройство автоматически отозвано."
    return "Подписка неактивна. Устройство автоматически отозвано."


def revoke_subscription_devices(subscription: Subscription) -> int:
    from lowlands_vpn.vpn import (
        VpnProvisioningError,
        is_vpn_auto_provisioning_enabled,
        revoke_device_on_server,
    )

    active_devices = subscription.devices.filter(
        Device.status != "revoked"
    ).all()
    if not active_devices:
        return 0

    revoke_reason = get_subscription_revoke_reason(subscription)
    revoked_count = 0

    for device in active_devices:
        server_error = None
        if is_vpn_auto_provisioning_enabled():
            try:
                revoke_device_on_server(device)
            except VpnProvisioningError as error:
                server_error = str(error)

        device.mark_revoked()
        device.last_error = (
            f"{revoke_reason} Ошибка серверного отзыва: {server_error}"
            if server_error
            else revoke_reason
        )
        revoked_count += 1

    return revoked_count


def sync_user_subscriptions(user: User) -> dict[str, int]:
    subscriptions = user.subscriptions.order_by(
        Subscription.created_at.desc()
    ).all()
    changed = False
    updated_statuses = 0
    auto_revoked_devices = 0

    for subscription in subscriptions:
        previous_status = subscription.status
        subscription.sync_status()
        if subscription.status != previous_status:
            changed = True
            updated_statuses += 1

        if not subscription.is_active():
            revoked_now = revoke_subscription_devices(subscription)
            if revoked_now:
                changed = True
                auto_revoked_devices += revoked_now

    if changed:
        db.session.commit()

    return {
        "updated_statuses": updated_statuses,
        "auto_revoked_devices": auto_revoked_devices,
    }


def get_current_subscription(user: User) -> Subscription | None:
    sync_user_subscriptions(user)
    active_subscription = (
        user.subscriptions.filter_by(status="active")
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if active_subscription:
        return active_subscription
    return user.subscriptions.order_by(Subscription.created_at.desc()).first()


def get_pending_subscription_request(user: User) -> Invoice | None:
    return (
        user.invoices.filter_by(
            type=SUBSCRIPTION_REQUEST_TYPE,
            status="pending",
        )
        .order_by(Invoice.created_at.desc())
        .first()
    )


def create_subscription_request(user: User, tariff: Tariff) -> Invoice:
    current_subscription = get_current_subscription(user)
    request_kind = "new_subscription"

    if current_subscription:
        request_kind = (
            "renewal"
            if current_subscription.tariff_id == tariff.id
            else "plan_change"
        )

    invoice = Invoice(
        user_id=user.id,
        subscription_id=current_subscription.id
        if current_subscription
        else None,
        amount_cents=tariff.price_cents,
        status="pending",
        type=SUBSCRIPTION_REQUEST_TYPE,
        review_channel="manual_review",
        description=f"Запрос на тариф {tariff.name}",
    )
    invoice.set_metadata(
        {
            "tariff_id": tariff.id,
            "tariff_name": tariff.name,
            "request_kind": request_kind,
            "requested_at": utc_now().isoformat(),
        }
    )
    db.session.add(invoice)
    return invoice


def approve_subscription_request(
    invoice: Invoice, reviewer_id: str
) -> Subscription:
    metadata = invoice.get_metadata()
    tariff_id = metadata.get("tariff_id")
    tariff = db.session.get(Tariff, tariff_id)
    if tariff is None:
        raise ValueError("Запрошенный тариф не найден.")

    subscription = invoice.subscription
    now = utc_now()

    if subscription is None:
        subscription = Subscription(
            user_id=invoice.user_id,
            tariff_id=tariff.id,
            starts_at=now,
            expires_at=now + timedelta(days=tariff.days_valid),
            traffic_limit_bytes=tariff.traffic_limit_bytes,
            status="active",
            is_lifetime=invoice.user.is_admin,
        )
        db.session.add(subscription)
        db.session.flush()
    else:
        subscription.renew(tariff)

    invoice.subscription_id = subscription.id
    invoice.review_channel = "manual_review"
    invoice.mark_as_approved(reference_id=f"manual:{reviewer_id}")
    metadata["reviewed_by"] = reviewer_id
    metadata["reviewed_at"] = utc_now().isoformat()
    invoice.set_metadata(metadata)
    return subscription


def delete_user_account(user: User) -> dict[str, int]:
    from lowlands_vpn.vpn import (
        VpnProvisioningError,
        is_vpn_auto_provisioning_enabled,
        revoke_device_on_server,
    )

    devices = (
        Device.query.join(Subscription)
        .filter(Subscription.user_id == user.id)
        .all()
    )
    vpn_cleanup_errors: list[str] = []
    vpn_cleanup_attempts = 0

    if is_vpn_auto_provisioning_enabled():
        for device in devices:
            if not device.vpn_uuid and not device.vpn_email:
                continue
            vpn_cleanup_attempts += 1
            try:
                revoke_device_on_server(device)
            except VpnProvisioningError as error:
                vpn_cleanup_errors.append(f"{device.name}: {error}")

    if vpn_cleanup_errors:
        raise VpnProvisioningError(
            "Не удалось очистить VPN-клиентов перед удалением пользователя: "
            + "; ".join(vpn_cleanup_errors)
        )

    summary = {
        "devices_deleted": len(devices),
        "subscriptions_deleted": user.subscriptions.count(),
        "invoices_deleted": user.invoices.count(),
        "vpn_cleanup_attempts": vpn_cleanup_attempts,
    }
    db.session.delete(user)
    db.session.commit()
    return summary

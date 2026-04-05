from datetime import timedelta

from lowlands_vpn.extensions import db
from lowlands_vpn.models import Invoice, Subscription, Tariff, User, utc_now

SUBSCRIPTION_REQUEST_TYPE = "subscription_request"


def sync_user_subscriptions(user: User) -> None:
    subscriptions = user.subscriptions.order_by(Subscription.created_at.desc()).all()
    changed = False

    for subscription in subscriptions:
        previous_status = subscription.status
        subscription.sync_status()
        if subscription.status != previous_status:
            changed = True

    if changed:
        db.session.commit()


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
            "renewal" if current_subscription.tariff_id == tariff.id else "plan_change"
        )

    invoice = Invoice(
        user_id=user.id,
        subscription_id=current_subscription.id if current_subscription else None,
        amount_cents=tariff.price_cents,
        status="pending",
        type=SUBSCRIPTION_REQUEST_TYPE,
        payment_system="manual_review",
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


def approve_subscription_request(invoice: Invoice, reviewer_id: str) -> Subscription:
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
        )
        db.session.add(subscription)
        db.session.flush()
    else:
        subscription.renew(tariff)

    invoice.subscription_id = subscription.id
    invoice.payment_system = "manual_review"
    invoice.mark_as_paid(payment_system_id=f"manual:{reviewer_id}")
    metadata["reviewed_by"] = reviewer_id
    metadata["reviewed_at"] = utc_now().isoformat()
    invoice.set_metadata(metadata)
    return subscription

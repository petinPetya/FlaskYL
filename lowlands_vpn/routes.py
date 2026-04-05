from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from lowlands_vpn.data import PLANS
from lowlands_vpn.extensions import db
from lowlands_vpn.forms import (
    AdminActionForm,
    AdminDeviceManagementForm,
    BalanceAdjustmentForm,
    DeviceActionForm,
    DeviceCreateForm,
    LoginForm,
    LogoutForm,
    RegisterForm,
    SubscriptionRequestForm,
)
from lowlands_vpn.models import Device, Invoice, Subscription, Tariff, User, utc_now
from lowlands_vpn.services import (
    SUBSCRIPTION_REQUEST_TYPE,
    approve_subscription_request,
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
    remove_server_vless_client_by_uuid,
    revoke_device_on_server,
)

main_bp = Blueprint("main", __name__)


@main_bp.app_context_processor
def inject_logout_form() -> dict[str, LogoutForm]:
    return {"logout_form": LogoutForm()}


def is_safe_redirect_url(target: str | None) -> bool:
    if not target:
        return False

    target_url = urlsplit(target)
    return not target_url.netloc and target_url.path.startswith("/")


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped_view(*args, **kwargs):
        if not current_user.is_admin:
            flash("Доступ к админ-панели разрешен только администраторам.", "warning")
            return redirect(url_for("main.dashboard"))
        return view_func(*args, **kwargs)

    return wrapped_view


def build_plan_cards(tariffs: list[Tariff]) -> list[dict]:
    features_by_name = {plan["name"]: plan.get("features", []) for plan in PLANS}
    cards = []

    for tariff in tariffs:
        cards.append(
            {
                "id": tariff.id,
                "name": tariff.name,
                "price": f"{tariff.price_cents / 100:.0f}",
                "period": f"{tariff.days_valid} дней",
                "description": tariff.description,
                "features": features_by_name.get(
                    tariff.name,
                    [
                        f"До {tariff.device_limit} устройств",
                        "Личный кабинет и поддержка",
                    ],
                ),
                "is_popular": tariff.is_popular,
            }
        )

    return cards


def get_invoice_tariff(invoice: Invoice) -> Tariff | None:
    tariff_id = invoice.get_requested_tariff_id()
    if not tariff_id:
        return None
    return db.session.get(Tariff, tariff_id)


def is_invoice_removable(invoice: Invoice) -> bool:
    return (
        invoice.type == SUBSCRIPTION_REQUEST_TYPE
        and invoice.status == "paid"
    )


def is_subscription_removable(subscription: Subscription) -> bool:
    return subscription.status == "revoked"


def is_device_removable(device: Device) -> bool:
    return device.status == "revoked"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "недоступно"
    if value < 1024:
        return f"{value} B"

    units = ["KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        size /= 1024
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
    return f"{value} B"


@main_bp.route("/")
def index():
    tariffs = (
        Tariff.query.filter_by(is_active=True)
        .order_by(Tariff.sort_order.asc(), Tariff.name.asc())
        .all()
    )
    return render_template("index.html", plans=build_plan_cards(tariffs))


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(db.select(User).where(User.email == email))

        if user and user.check_password(form.password.data):
            if not user.is_active:
                flash(
                    "Аккаунт деактивирован администратором. Обратитесь в поддержку.",
                    "danger",
                )
                return render_template("login.html", form=form)
            user.last_login_at = utc_now()
            db.session.commit()
            login_user(user)
            next_page = request.args.get("next")
            flash("Вы успешно вошли в аккаунт.", "success")
            if is_safe_redirect_url(next_page):
                return redirect(next_page)
            return redirect(url_for("main.dashboard"))

        flash("Неверный email или пароль.", "danger")

    return render_template("login.html", form=form)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        admin_exists = db.session.scalar(
            db.select(User.id).where(User.is_admin.is_(True)).limit(1)
        )

        user = User(
            email=form.email.data.strip().lower(),
            password=form.password.data,
            is_admin=not bool(admin_exists),
            last_login_at=utc_now(),
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(
            "Аккаунт создан. Теперь можно выбрать тариф в личном кабинете.", "success"
        )
        if user.is_admin:
            flash(
                "Это первый аккаунт в системе, поэтому ему выданы права администратора.",
                "info",
            )
        return redirect(url_for("main.dashboard"))

    return render_template("register.html", form=form)


@main_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Вы вышли из аккаунта.", "info")
    return redirect(url_for("main.index"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    current_subscription = get_current_subscription(current_user)
    latest_invoice = current_user.invoices.order_by(Invoice.created_at.desc()).first()
    pending_request = get_pending_subscription_request(current_user)
    pending_request_tariff = (
        get_invoice_tariff(pending_request) if pending_request else None
    )
    tariffs = (
        Tariff.query.filter_by(is_active=True)
        .order_by(Tariff.sort_order.asc(), Tariff.name.asc())
        .all()
    )
    devices = []

    if current_subscription:
        devices = current_subscription.devices.order_by(Device.created_at.desc()).all()

    return render_template(
        "dashboard.html",
        current_subscription=current_subscription,
        latest_invoice=latest_invoice,
        pending_request=pending_request,
        pending_request_tariff=pending_request_tariff,
        tariffs=tariffs,
        devices=devices,
        vpn_auto_provisioning_enabled=is_vpn_auto_provisioning_enabled(),
        request_form=SubscriptionRequestForm(),
        device_form=DeviceCreateForm(),
        device_action_form=DeviceActionForm(),
    )


@main_bp.route("/subscriptions/request", methods=["POST"])
@login_required
def request_subscription():
    form = SubscriptionRequestForm()
    if not form.validate_on_submit():
        flash("Выберите корректный тариф.", "danger")
        return redirect(url_for("main.dashboard"))

    pending_request = get_pending_subscription_request(current_user)
    if pending_request is not None:
        flash(
            "У вас уже есть необработанный запрос на подключение или продление.",
            "warning",
        )
        return redirect(url_for("main.dashboard"))

    tariff = db.session.get(Tariff, form.tariff_id.data)
    if tariff is None or not tariff.is_active:
        flash("Выбранный тариф недоступен.", "danger")
        return redirect(url_for("main.dashboard"))

    create_subscription_request(current_user, tariff)
    db.session.commit()
    flash(
        "Запрос на тариф создан. После ручной проверки он появится в истории кабинета.",
        "success",
    )
    return redirect(url_for("main.dashboard"))


@main_bp.route("/devices", methods=["POST"])
@login_required
def add_device():
    form = DeviceCreateForm()
    current_subscription = get_current_subscription(current_user)
    is_admin_unlimited = current_user.is_admin

    if not form.validate_on_submit():
        flash("Проверьте название устройства и выбранную платформу.", "danger")
        return redirect(url_for("main.dashboard"))

    if current_subscription is None or not current_subscription.is_active():
        flash("Добавлять устройства можно только для активной подписки.", "warning")
        return redirect(url_for("main.dashboard"))

    if not is_admin_unlimited and not current_subscription.can_add_device():
        flash("Лимит устройств по тарифу уже исчерпан.", "warning")
        return redirect(url_for("main.dashboard"))

    device = Device(
        subscription_id=current_subscription.id,
        name=form.name.data.strip(),
        platform=form.platform.data,
        status="pending",
        provisioning_state="requested",
    )
    db.session.add(device)
    db.session.flush()

    if is_vpn_auto_provisioning_enabled():
        try:
            provision_device(device)
        except VpnProvisioningError as error:
            device.mark_failed(str(error))
            db.session.commit()
            flash(
                "Устройство добавлено, но выдача VPN завершилась ошибкой. "
                "Попробуйте позже или перепроверьте SSH/Xray.",
                "warning",
            )
            return redirect(url_for("main.dashboard"))

        device.mark_ready()
        db.session.commit()
        flash("Устройство добавлено, VPN-ссылка готова.", "success")
        return redirect(url_for("main.dashboard"))

    db.session.commit()
    flash(
        "Устройство добавлено. Автовыдача VPN не настроена, поэтому слот ждёт обработки.",
        "success",
    )
    return redirect(url_for("main.dashboard"))


@main_bp.route("/devices/<string:device_id>/revoke", methods=["POST"])
@login_required
def revoke_device(device_id):
    form = DeviceActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на отзыв устройства.", "danger")
        return redirect(url_for("main.dashboard"))

    device = (
        Device.query.join(Subscription)
        .filter(Device.id == device_id, Subscription.user_id == current_user.id)
        .first()
    )
    if device is None:
        flash("Устройство не найдено.", "warning")
        return redirect(url_for("main.dashboard"))

    if device.status == "revoked":
        flash("Устройство уже отозвано.", "info")
        return redirect(url_for("main.dashboard"))

    try:
        revoke_device_on_server(device)
    except VpnProvisioningError as error:
        device.record_provisioning_error(str(error))
        db.session.commit()
        flash("Не удалось отозвать VPN-конфиг устройства.", "danger")
        return redirect(url_for("main.dashboard"))

    device.mark_revoked()
    db.session.commit()
    flash("Устройство отозвано.", "info")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/admin")
@admin_required
def admin_dashboard():
    for user in User.query.all():
        sync_user_subscriptions(user)

    stats = {
        "users_total": User.query.count(),
        "admins_total": User.query.filter_by(is_admin=True).count(),
        "subscriptions_total": Subscription.query.count(),
        "subscriptions_active": Subscription.query.filter_by(status="active").count(),
        "invoices_total": Invoice.query.count(),
        "invoices_paid": Invoice.query.filter_by(status="paid").count(),
        "requests_pending": Invoice.query.filter_by(
            type=SUBSCRIPTION_REQUEST_TYPE, status="pending"
        ).count(),
        "devices_total": Device.query.count(),
        "devices_ready": Device.query.filter_by(provisioning_state="ready").count(),
    }
    recent_users = User.query.order_by(User.created_at.desc()).limit(8).all()
    recent_invoices = Invoice.query.order_by(Invoice.created_at.desc()).limit(8).all()
    pending_requests = (
        Invoice.query.filter_by(type=SUBSCRIPTION_REQUEST_TYPE, status="pending")
        .order_by(Invoice.created_at.desc())
        .limit(8)
        .all()
    )
    recent_devices = Device.query.order_by(Device.created_at.desc()).limit(8).all()
    tariffs = Tariff.query.order_by(Tariff.sort_order.asc(), Tariff.name.asc()).all()
    server_vless_clients = []
    server_vless_error = None
    server_vless_stats_enabled = False

    if is_vpn_auto_provisioning_enabled():
        try:
            server_payload = list_server_vless_clients()
            server_vless_stats_enabled = server_payload["stats_enabled"]
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

            for client in server_payload["clients"]:
                local_device = device_by_uuid.get(client.get("uuid"))
                server_vless_clients.append(
                    {
                        **client,
                        "device": local_device,
                        "user": local_device.subscription.user if local_device else None,
                    }
                )
        except VpnProvisioningError as error:
            server_vless_error = str(error)

    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_users=recent_users,
        recent_invoices=recent_invoices,
        pending_requests=pending_requests,
        recent_devices=recent_devices,
        tariffs=tariffs,
        server_vless_clients=server_vless_clients,
        server_vless_error=server_vless_error,
        server_vless_stats_enabled=server_vless_stats_enabled,
        vpn_auto_provisioning_enabled=is_vpn_auto_provisioning_enabled(),
        action_form=AdminActionForm(),
        get_invoice_tariff=get_invoice_tariff,
        is_invoice_removable=is_invoice_removable,
        format_bytes=format_bytes,
    )


@main_bp.route("/admin/users")
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@main_bp.route("/admin/users/<string:user_id>")
@admin_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("main.admin_users"))

    sync_user_subscriptions(user)
    subscriptions = user.subscriptions.order_by(Subscription.created_at.desc()).all()
    invoices = user.invoices.order_by(Invoice.created_at.desc()).all()
    devices = (
        Device.query.join(Subscription)
        .filter(Subscription.user_id == user.id)
        .order_by(Device.created_at.desc())
        .all()
    )
    revoked_devices_count = sum(1 for device in devices if is_device_removable(device))
    role_form = AdminActionForm(prefix="role")
    status_form = AdminActionForm(prefix="status")
    deposit_form = BalanceAdjustmentForm(prefix="deposit")
    charge_form = BalanceAdjustmentForm(prefix="charge")
    invoice_action_form = AdminActionForm()
    device_forms = {}

    for device in devices:
        form = AdminDeviceManagementForm(prefix=f"device-{device.id}")
        form.status.data = device.status
        form.provisioning_state.data = device.provisioning_state
        form.assigned_ip.data = device.assigned_ip
        form.last_error.data = device.last_error
        device_forms[device.id] = form

    return render_template(
        "admin/user_detail.html",
        user=user,
        subscriptions=subscriptions,
        invoices=invoices,
        devices=devices,
        vpn_auto_provisioning_enabled=is_vpn_auto_provisioning_enabled(),
        role_form=role_form,
        status_form=status_form,
        deposit_form=deposit_form,
        charge_form=charge_form,
        invoice_action_form=invoice_action_form,
        vpn_action_form=AdminActionForm(),
        device_forms=device_forms,
        revoked_devices_count=revoked_devices_count,
        get_invoice_tariff=get_invoice_tariff,
        is_invoice_removable=is_invoice_removable,
        is_subscription_removable=is_subscription_removable,
        is_device_removable=is_device_removable,
    )


@main_bp.route("/admin/users/<string:user_id>/toggle-admin", methods=["POST"])
@admin_required
def admin_toggle_user_admin(user_id):
    form = AdminActionForm(prefix="role")
    if not form.validate_on_submit():
        flash("Некорректный запрос на изменение роли.", "danger")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("main.admin_users"))

    if user.id == current_user.id and user.is_admin:
        flash("Нельзя снять права администратора с собственного аккаунта.", "warning")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    user.is_admin = not user.is_admin
    db.session.commit()
    flash("Роль пользователя обновлена.", "success")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/users/<string:user_id>/toggle-active", methods=["POST"])
@admin_required
def admin_toggle_user_active(user_id):
    form = AdminActionForm(prefix="status")
    if not form.validate_on_submit():
        flash("Некорректный запрос на изменение статуса.", "danger")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("main.admin_users"))

    if user.id == current_user.id and user.is_active:
        flash("Нельзя деактивировать собственный аккаунт.", "warning")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    user.is_active = not user.is_active
    db.session.commit()
    flash("Статус пользователя обновлен.", "success")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/users/<string:user_id>/deposit", methods=["POST"])
@admin_required
def admin_deposit_balance(user_id):
    form = BalanceAdjustmentForm(prefix="deposit")
    if not form.validate_on_submit():
        flash("Введите корректную сумму для пополнения.", "danger")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("main.admin_users"))

    user.deposit(form.amount_rub.data * 100)
    db.session.commit()
    flash("Баланс пополнен.", "success")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/users/<string:user_id>/charge", methods=["POST"])
@admin_required
def admin_charge_balance(user_id):
    form = BalanceAdjustmentForm(prefix="charge")
    if not form.validate_on_submit():
        flash("Введите корректную сумму для списания.", "danger")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("main.admin_users"))

    try:
        user.charge(form.amount_rub.data * 100)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("main.admin_user_detail", user_id=user_id))

    db.session.commit()
    flash("Средства списаны с баланса.", "success")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/invoices/<string:invoice_id>/approve", methods=["POST"])
@admin_required
def admin_approve_invoice(invoice_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на подтверждение.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None or invoice.type != SUBSCRIPTION_REQUEST_TYPE:
        flash("Запрос не найден.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if invoice.status != "pending":
        flash("Этот запрос уже обработан.", "warning")
        return redirect(url_for("main.admin_user_detail", user_id=invoice.user_id))

    try:
        approve_subscription_request(invoice, current_user.id)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("main.admin_user_detail", user_id=invoice.user_id))

    db.session.commit()
    flash("Запрос подтвержден, подписка обновлена.", "success")
    return redirect(url_for("main.admin_user_detail", user_id=invoice.user_id))


@main_bp.route("/admin/invoices/<string:invoice_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_invoice(invoice_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на отмену.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None or invoice.type != SUBSCRIPTION_REQUEST_TYPE:
        flash("Запрос не найден.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if invoice.status != "pending":
        flash("Этот запрос уже обработан.", "warning")
        return redirect(url_for("main.admin_user_detail", user_id=invoice.user_id))

    metadata = invoice.get_metadata()
    metadata["reviewed_by"] = current_user.id
    metadata["reviewed_at"] = utc_now().isoformat()
    invoice.set_metadata(metadata)
    invoice.mark_as_cancelled("Отменено администратором")
    db.session.commit()
    flash("Запрос отменен.", "info")
    return redirect(url_for("main.admin_user_detail", user_id=invoice.user_id))


@main_bp.route("/admin/invoices/<string:invoice_id>/delete", methods=["POST"])
@admin_required
def admin_delete_invoice(invoice_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на удаление.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None:
        flash("Запись не найдена.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if not is_invoice_removable(invoice):
        flash("Удалять можно только оплаченные запросы на подписку.", "warning")
        return redirect(url_for("main.admin_user_detail", user_id=invoice.user_id))

    user_id = invoice.user_id
    db.session.delete(invoice)
    db.session.commit()
    flash("Запись об оплаченной заявке удалена.", "info")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/subscriptions/<string:subscription_id>/delete", methods=["POST"])
@admin_required
def admin_delete_subscription(subscription_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на удаление.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    subscription = db.session.get(Subscription, subscription_id)
    if subscription is None:
        flash("Подписка не найдена.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if not is_subscription_removable(subscription):
        flash("Удалять можно только отозванные подписки.", "warning")
        return redirect(
            url_for("main.admin_user_detail", user_id=subscription.user_id)
        )

    user_id = subscription.user_id
    for invoice in subscription.invoices.all():
        invoice.subscription_id = None

    db.session.delete(subscription)
    db.session.commit()
    flash("Отозванная подписка удалена из истории.", "info")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/devices/<string:device_id>/update", methods=["POST"])
@admin_required
def admin_update_device(device_id):
    device = db.session.get(Device, device_id)
    if device is None:
        flash("Устройство не найдено.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    form = AdminDeviceManagementForm(prefix=f"device-{device.id}")
    if not form.validate_on_submit():
        flash("Проверьте поля устройства и повторите попытку.", "danger")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    assigned_ip = form.assigned_ip.data.strip() if form.assigned_ip.data else None
    last_error = form.last_error.data.strip() if form.last_error.data else None

    if form.status.data == "revoked" or form.provisioning_state.data == "revoked":
        device.mark_revoked()
    elif form.provisioning_state.data == "ready":
        device.mark_ready(assigned_ip)
        device.status = form.status.data
    elif form.provisioning_state.data == "failed":
        device.mark_failed(last_error or "Устройство не было выдано.")
        device.status = form.status.data
        device.assigned_ip = assigned_ip
    else:
        device.status = form.status.data
        device.provisioning_state = form.provisioning_state.data
        device.assigned_ip = assigned_ip
        device.last_error = last_error
        if form.provisioning_state.data == "queued":
            device.last_error = None

    db.session.commit()
    flash("Параметры устройства обновлены.", "success")
    return redirect(
        url_for("main.admin_user_detail", user_id=device.subscription.user_id)
    )


@main_bp.route("/admin/devices/<string:device_id>/provision", methods=["POST"])
@admin_required
def admin_provision_device(device_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на выдачу VPN.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    device = db.session.get(Device, device_id)
    if device is None:
        flash("Устройство не найдено.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if device.status == "revoked":
        flash("Нельзя перевыдать уже отозванное устройство.", "warning")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    if not is_vpn_auto_provisioning_enabled():
        flash("Автовыдача VPN не настроена в переменных окружения.", "warning")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    device.mark_requested()
    try:
        provision_device(device)
    except VpnProvisioningError as error:
        device.mark_failed(str(error))
        db.session.commit()
        flash("Выдача VPN завершилась ошибкой.", "danger")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    device.mark_ready(device.assigned_ip)
    db.session.commit()
    flash("VPN для устройства подготовлен.", "success")
    return redirect(
        url_for("main.admin_user_detail", user_id=device.subscription.user_id)
    )


@main_bp.route("/admin/devices/<string:device_id>/revoke", methods=["POST"])
@admin_required
def admin_revoke_device(device_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на отзыв VPN.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    device = db.session.get(Device, device_id)
    if device is None:
        flash("Устройство не найдено.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if device.status == "revoked":
        flash("Устройство уже отозвано.", "info")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    try:
        revoke_device_on_server(device)
    except VpnProvisioningError as error:
        device.record_provisioning_error(str(error))
        db.session.commit()
        flash("Не удалось отозвать VPN-конфиг устройства.", "danger")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    device.mark_revoked()
    db.session.commit()
    flash("VPN устройства отозван.", "info")
    return redirect(
        url_for("main.admin_user_detail", user_id=device.subscription.user_id)
    )


@main_bp.route("/admin/devices/<string:device_id>/delete", methods=["POST"])
@admin_required
def admin_delete_device(device_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на удаление.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    device = db.session.get(Device, device_id)
    if device is None:
        flash("Устройство не найдено.", "warning")
        return redirect(url_for("main.admin_dashboard"))

    if not is_device_removable(device):
        flash("Удалять можно только отозванные устройства.", "warning")
        return redirect(
            url_for("main.admin_user_detail", user_id=device.subscription.user_id)
        )

    user_id = device.subscription.user_id
    db.session.delete(device)
    db.session.commit()
    flash("Запись об отозванном устройстве удалена.", "info")
    return redirect(url_for("main.admin_user_detail", user_id=user_id))


@main_bp.route("/admin/vpn/clients/<string:client_uuid>/delete", methods=["POST"])
@admin_required
def admin_delete_server_vless_client(client_uuid):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на удаление VLESS-ссылки.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    try:
        remove_server_vless_client_by_uuid(client_uuid)
    except VpnProvisioningError as error:
        flash(f"Не удалось удалить VLESS-ссылку: {error}", "danger")
        return redirect(url_for("main.admin_dashboard"))

    linked_device = db.session.scalar(
        db.select(Device).where(Device.vpn_uuid == client_uuid)
    )
    if linked_device is not None and linked_device.status != "revoked":
        linked_device.mark_revoked()
        db.session.commit()
    elif linked_device is not None:
        linked_device.vpn_link = None
        db.session.commit()

    flash("VLESS-ссылка удалена с сервера.", "info")
    return redirect(url_for("main.admin_dashboard"))


@main_bp.route("/admin/users/<string:user_id>/devices/delete-revoked", methods=["POST"])
@admin_required
def admin_delete_revoked_devices(user_id):
    form = AdminActionForm()
    if not form.validate_on_submit():
        flash("Некорректный запрос на удаление.", "danger")
        return redirect(url_for("main.admin_dashboard"))

    user = db.session.get(User, user_id)
    if user is None:
        flash("Пользователь не найден.", "warning")
        return redirect(url_for("main.admin_users"))

    revoked_devices = (
        Device.query.join(Subscription)
        .filter(
            Subscription.user_id == user.id,
            Device.status == "revoked",
        )
        .all()
    )

    if not revoked_devices:
        flash("У пользователя нет отозванных устройств для очистки.", "info")
        return redirect(url_for("main.admin_user_detail", user_id=user.id))

    removed_count = len(revoked_devices)
    for device in revoked_devices:
        db.session.delete(device)

    db.session.commit()
    flash(f"Удалено отозванных устройств: {removed_count}.", "info")
    return redirect(url_for("main.admin_user_detail", user_id=user.id))

from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from lowlands_vpn.data import PLANS
from lowlands_vpn.extensions import db
from lowlands_vpn.forms import (
    AdminActionForm,
    BalanceAdjustmentForm,
    LoginForm,
    LogoutForm,
    RegisterForm,
)
from lowlands_vpn.models import Invoice, Subscription, Tariff, User

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


@main_bp.route("/")
def index():
    return render_template("index.html", plans=PLANS)


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
            user.last_login_at = datetime.utcnow()
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
        selected_tariff = Tariff.query.filter_by(
            name=form.plan.data, is_active=True
        ).first()
        if selected_tariff is None:
            flash("Выбранный тариф недоступен. Обновите страницу.", "danger")
            return render_template("register.html", form=form)

        user = User(
            email=form.email.data.strip().lower(),
            password=form.password.data,
            is_admin=not bool(admin_exists),
            last_login_at=datetime.utcnow(),
        )
        db.session.add(user)
        db.session.flush()

        now = datetime.utcnow()
        subscription = Subscription(
            user_id=user.id,
            tariff_id=selected_tariff.id,
            starts_at=now,
            expires_at=now + timedelta(days=selected_tariff.days_valid or 36500),
            traffic_limit_bytes=selected_tariff.traffic_limit_bytes,
            status="active",
        )
        db.session.add(subscription)
        db.session.commit()
        login_user(user)
        flash(
            "Аккаунт создан, а стартовая подписка на выбранный тариф активирована.",
            "success",
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
    current_subscription = current_user.subscriptions.order_by(
        Subscription.created_at.desc()
    ).first()
    latest_invoice = current_user.invoices.order_by(Invoice.created_at.desc()).first()
    return render_template(
        "dashboard.html",
        current_subscription=current_subscription,
        latest_invoice=latest_invoice,
    )


@main_bp.route("/admin")
@admin_required
def admin_dashboard():
    stats = {
        "users_total": User.query.count(),
        "admins_total": User.query.filter_by(is_admin=True).count(),
        "subscriptions_total": Subscription.query.count(),
        "subscriptions_active": Subscription.query.filter_by(status="active").count(),
        "invoices_total": Invoice.query.count(),
        "invoices_paid": Invoice.query.filter_by(status="paid").count(),
    }
    recent_users = User.query.order_by(User.created_at.desc()).limit(8).all()
    recent_invoices = Invoice.query.order_by(Invoice.created_at.desc()).limit(8).all()
    tariffs = Tariff.query.order_by(Tariff.sort_order.asc(), Tariff.name.asc()).all()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_users=recent_users,
        recent_invoices=recent_invoices,
        tariffs=tariffs,
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

    subscriptions = user.subscriptions.order_by(Subscription.created_at.desc()).all()
    invoices = user.invoices.order_by(Invoice.created_at.desc()).all()
    role_form = AdminActionForm(prefix="role")
    status_form = AdminActionForm(prefix="status")
    deposit_form = BalanceAdjustmentForm(prefix="deposit")
    charge_form = BalanceAdjustmentForm(prefix="charge")
    return render_template(
        "admin/user_detail.html",
        user=user,
        subscriptions=subscriptions,
        invoices=invoices,
        role_form=role_form,
        status_form=status_form,
        deposit_form=deposit_form,
        charge_form=charge_form,
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

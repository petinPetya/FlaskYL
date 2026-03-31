from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from lowlands_vpn.data import PLANS
from lowlands_vpn.extensions import db
from lowlands_vpn.forms import LoginForm, LogoutForm, RegisterForm
from lowlands_vpn.models import User

main_bp = Blueprint("main", __name__)


@main_bp.app_context_processor
def inject_logout_form() -> dict[str, LogoutForm]:
    return {"logout_form": LogoutForm()}


def is_safe_redirect_url(target: str | None) -> bool:
    if not target:
        return False

    target_url = urlsplit(target)
    return not target_url.netloc and target_url.path.startswith("/")


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
        user = User(
            name=form.name.data.strip(),
            email=form.email.data.strip().lower(),
            plan_name=form.plan.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Аккаунт создан. Добро пожаловать в личный кабинет.", "success")
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
    return render_template("dashboard.html")

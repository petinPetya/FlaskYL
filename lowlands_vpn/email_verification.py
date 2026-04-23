from __future__ import annotations

import smtplib
from datetime import timedelta
from email.message import EmailMessage

from flask import current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from lowlands_vpn.extensions import db
from lowlands_vpn.models import User, utc_now


class EmailVerificationError(RuntimeError):
    pass


def is_email_verification_enabled() -> bool:
    return bool(current_app.config.get("EMAIL_VERIFICATION_ENABLED", True))


def is_email_verification_required() -> bool:
    return bool(
        is_email_verification_enabled()
        and current_app.config.get("EMAIL_VERIFICATION_REQUIRED", False)
    )


def _token_ttl_seconds() -> int:
    return max(int(current_app.config.get("EMAIL_VERIFICATION_TOKEN_TTL_SECONDS", 86400)), 1)


def _resend_cooldown_seconds() -> int:
    return max(
        int(current_app.config.get("EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS", 60)),
        0,
    )


def _token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt=current_app.config.get("EMAIL_VERIFICATION_SALT", "email-verification"),
    )


def generate_email_verification_token(user: User) -> str:
    return _token_serializer().dumps(
        {
            "user_id": user.id,
            "email": user.email,
        }
    )


def get_email_verification_url(user: User) -> str:
    token = generate_email_verification_token(user)
    path = url_for("main.verify_email", token=token)
    public_app_url = (current_app.config.get("PUBLIC_APP_URL") or "").strip()
    if public_app_url:
        return f"{public_app_url.rstrip('/')}{path}"
    return url_for("main.verify_email", token=token, _external=True)


def _deliver_verification_email(user: User, verification_url: str) -> bool:
    smtp_host = (current_app.config.get("SMTP_HOST") or "").strip()
    if not smtp_host:
        current_app.logger.info(
            "Email verification link for %s: %s",
            user.email,
            verification_url,
        )
        return False

    smtp_port = int(current_app.config.get("SMTP_PORT", 587))
    smtp_use_tls = bool(current_app.config.get("SMTP_USE_TLS", True))
    smtp_use_ssl = bool(current_app.config.get("SMTP_USE_SSL", False))
    smtp_username = (current_app.config.get("SMTP_USERNAME") or "").strip()
    smtp_password = current_app.config.get("SMTP_PASSWORD") or ""
    sender = (
        (current_app.config.get("SMTP_FROM") or "").strip()
        or smtp_username
        or "no-reply@localhost"
    )

    message = EmailMessage()
    message["Subject"] = "Подтверждение email для Lowlands VPN"
    message["From"] = sender
    message["To"] = user.email
    message.set_content(
        "Подтвердите ваш email, чтобы завершить настройку аккаунта.\n\n"
        f"Ссылка подтверждения:\n{verification_url}\n\n"
        f"Срок действия ссылки: {_token_ttl_seconds()} секунд.\n"
    )

    if smtp_use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as smtp:
            if smtp_username:
                smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return True

    with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as smtp:
        smtp.ehlo()
        if smtp_use_tls:
            smtp.starttls()
            smtp.ehlo()
        if smtp_username:
            smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)
    return True


def send_email_verification_message(
    user: User, *, ignore_cooldown: bool = False
) -> dict[str, bool | str | None]:
    if not is_email_verification_enabled():
        return {
            "enabled": False,
            "already_verified": user.is_email_verified,
            "delivered": False,
            "delivery": "disabled",
            "verification_url": None,
        }

    if user.is_email_verified:
        return {
            "enabled": True,
            "already_verified": True,
            "delivered": False,
            "delivery": "already_verified",
            "verification_url": None,
        }

    now = utc_now()
    if (
        not ignore_cooldown
        and user.email_verification_sent_at is not None
        and _resend_cooldown_seconds() > 0
    ):
        sent_delta = now - user.email_verification_sent_at
        cooldown = timedelta(seconds=_resend_cooldown_seconds())
        if sent_delta < cooldown:
            wait_seconds = int((cooldown - sent_delta).total_seconds())
            raise EmailVerificationError(
                f"Повторная отправка будет доступна через {wait_seconds} сек."
            )

    verification_url = get_email_verification_url(user)
    user.email_verification_sent_at = now
    db.session.commit()

    try:
        delivered = _deliver_verification_email(user, verification_url)
    except (OSError, smtplib.SMTPException) as error:
        raise EmailVerificationError(
            f"Не удалось отправить письмо подтверждения: {error}"
        ) from error

    return {
        "enabled": True,
        "already_verified": False,
        "delivered": delivered,
        "delivery": "smtp" if delivered else "log",
        "verification_url": verification_url,
    }


def verify_email_token(token: str) -> User:
    if not is_email_verification_enabled():
        raise EmailVerificationError("Подтверждение email отключено в конфигурации.")

    try:
        payload = _token_serializer().loads(token, max_age=_token_ttl_seconds())
    except SignatureExpired as error:
        raise EmailVerificationError("Срок действия ссылки подтверждения истек.") from error
    except BadSignature as error:
        raise EmailVerificationError("Ссылка подтверждения недействительна.") from error

    user_id = payload.get("user_id")
    token_email = payload.get("email")
    if not user_id or not token_email:
        raise EmailVerificationError("Ссылка подтверждения недействительна.")

    user = db.session.get(User, user_id)
    if user is None:
        raise EmailVerificationError("Пользователь для подтверждения не найден.")
    if user.email != token_email:
        raise EmailVerificationError(
            "Email пользователя изменился, запросите новую ссылку подтверждения."
        )

    if not user.is_email_verified:
        user.mark_email_verified()
        db.session.commit()

    return user

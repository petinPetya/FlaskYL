from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.fields.datetime import DateTimeLocalField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    IPAddress,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)

from lowlands_vpn.extensions import db
from lowlands_vpn.models import User

DEVICE_PLATFORM_CHOICES = [
    ("windows", "Windows"),
    ("macos", "macOS"),
    ("linux", "Linux"),
    ("ios", "iPhone / iPad"),
    ("android", "Android"),
]

DEVICE_STATUS_CHOICES = [
    ("pending", "pending"),
    ("active", "active"),
    ("revoked", "revoked"),
]

DEVICE_PROVISIONING_CHOICES = [
    ("requested", "requested"),
    ("queued", "queued"),
    ("ready", "ready"),
    ("failed", "failed"),
    ("revoked", "revoked"),
]


class RegisterForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[
            DataRequired(message="Введите email."),
            Email(message="Введите корректный email."),
            Length(max=255),
        ],
    )
    password = PasswordField(
        "Пароль",
        validators=[
            DataRequired(message="Введите пароль."),
            Length(min=8, message="Пароль должен содержать минимум 8 символов."),
        ],
    )
    confirm_password = PasswordField(
        "Подтверждение пароля",
        validators=[
            DataRequired(message="Повторите пароль."),
            EqualTo("password", message="Пароли не совпадают."),
        ],
    )
    submit = SubmitField("Зарегистрироваться")

    def validate_email(self, field: StringField) -> None:
        existing_user = db.session.scalar(
            db.select(User).where(User.email == field.data.strip().lower())
        )
        if existing_user:
            raise ValidationError("Пользователь с таким email уже существует.")


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[
            DataRequired(message="Введите email."),
            Email(message="Введите корректный email."),
        ],
    )
    password = PasswordField(
        "Пароль",
        validators=[DataRequired(message="Введите пароль.")],
    )
    submit = SubmitField("Войти")


class LogoutForm(FlaskForm):
    submit = SubmitField("Выйти")


class EmailVerificationResendForm(FlaskForm):
    submit = SubmitField("Отправить ссылку ещё раз")


class AdminActionForm(FlaskForm):
    submit = SubmitField("Подтвердить")


class AdminDeviceVpnEmailForm(FlaskForm):
    vpn_email = StringField(
        "Xray email",
        validators=[
            DataRequired(message="Укажите email в формате label@xray."),
            Length(max=255),
        ],
    )
    submit = SubmitField("Обновить email")

    def validate_vpn_email(self, field: StringField) -> None:
        email = (field.data or "").strip()
        if not email:
            raise ValidationError("Укажите email в формате label@xray.")
        if " " in email:
            raise ValidationError("Email не должен содержать пробелы.")


class BalanceAdjustmentForm(FlaskForm):
    amount_rub = IntegerField(
        "Сумма, ₽",
        validators=[
            DataRequired(message="Введите сумму."),
            NumberRange(min=1, message="Сумма должна быть больше нуля."),
        ],
    )
    submit = SubmitField("Изменить баланс")


class SubscriptionRequestForm(FlaskForm):
    tariff_id = HiddenField(validators=[DataRequired(message="Выберите тариф.")])
    submit = SubmitField("Оставить запрос")


class DeviceCreateForm(FlaskForm):
    name = StringField(
        "Название устройства",
        validators=[
            DataRequired(message="Укажите название устройства."),
            Length(max=120),
        ],
    )
    platform = SelectField(
        "Платформа",
        choices=DEVICE_PLATFORM_CHOICES,
        validators=[DataRequired(message="Выберите платформу.")],
    )
    submit = SubmitField("Добавить устройство")


class DeviceActionForm(FlaskForm):
    submit = SubmitField("Подтвердить")


class AdminDeviceManagementForm(FlaskForm):
    status = SelectField(
        "Статус устройства",
        choices=DEVICE_STATUS_CHOICES,
        validators=[DataRequired()],
    )
    provisioning_state = SelectField(
        "Состояние выдачи",
        choices=DEVICE_PROVISIONING_CHOICES,
        validators=[DataRequired()],
    )
    assigned_ip = StringField(
        "Выданный IP",
        validators=[
            Optional(),
            IPAddress(ipv4=True, ipv6=True, message="Введите корректный IP."),
            Length(max=64),
        ],
    )
    last_error = StringField(
        "Последняя ошибка",
        validators=[Optional(), Length(max=255)],
    )
    submit = SubmitField("Обновить устройство")


class AdminSubscriptionExpiryForm(FlaskForm):
    is_lifetime = BooleanField("Бессрочно")
    expires_at = DateTimeLocalField(
        "Срок действия до",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )
    submit = SubmitField("Обновить срок")

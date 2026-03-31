from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from lowlands_vpn.data import PLAN_CHOICES
from lowlands_vpn.extensions import db
from lowlands_vpn.models import User


class RegisterForm(FlaskForm):
    name = StringField(
        "Имя",
        validators=[
            DataRequired(message="Введите имя."),
            Length(
                min=2, max=120, message="Имя должно содержать от 2 до 120 символов."
            ),
        ],
    )
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
    plan = SelectField(
        "Тариф",
        choices=PLAN_CHOICES,
        validators=[DataRequired(message="Выберите тариф.")],
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

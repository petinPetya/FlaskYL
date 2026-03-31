from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "main.login"
login_manager.login_message = "Сначала войдите в аккаунт."
login_manager.login_message_category = "warning"

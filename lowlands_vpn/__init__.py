from flask import Flask, render_template
from flask_wtf.csrf import CSRFError

from lowlands_vpn.config import BASE_DIR, Config
from lowlands_vpn.database import init_database
from lowlands_vpn.extensions import csrf, db, login_manager


def create_app() -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    from lowlands_vpn.routes import main_bp

    app.register_blueprint(main_bp)
    register_error_handlers(app)

    with app.app_context():
        init_database(app.instance_path)

    return app


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return (
            render_template(
                "csrf_error.html",
                error_message=error.description or "Недействительный CSRF-токен.",
            ),
            400,
        )

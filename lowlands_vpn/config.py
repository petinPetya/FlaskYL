import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"


def get_env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def normalize_database_uri(database_uri: str | None) -> str:
    if not database_uri:
        return f"sqlite:///{INSTANCE_DIR / 'site.db'}"
    if database_uri.startswith("postgres://"):
        return database_uri.replace("postgres://", "postgresql://", 1)
    return database_uri


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
    SQLALCHEMY_DATABASE_URI = normalize_database_uri(os.environ.get("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    TESTING = False
    DEBUG = get_env_bool("FLASK_DEBUG", False)
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600
    WTF_CSRF_SSL_STRICT = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = get_env_bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = get_env_bool("REMEMBER_COOKIE_SECURE", False)
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "http")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
    VPN_AUTO_PROVISION = get_env_bool("VPN_AUTO_PROVISION", True)
    VPN_SSH_HOST = os.environ.get("VPN_SSH_HOST", "").strip()
    VPN_SSH_PORT = get_env_int("VPN_SSH_PORT", 22)
    VPN_SSH_USER = os.environ.get("VPN_SSH_USER", "").strip()
    VPN_SSH_KEY_PATH = os.environ.get("VPN_SSH_KEY_PATH", "").strip()
    VPN_SSH_CONFIG_FILE = os.environ.get("VPN_SSH_CONFIG_FILE", "/dev/null").strip()
    VPN_SSH_CONNECT_TIMEOUT = get_env_int("VPN_SSH_CONNECT_TIMEOUT", 10)
    VPN_SSH_STRICT_HOST_KEY_CHECKING = get_env_bool(
        "VPN_SSH_STRICT_HOST_KEY_CHECKING", True
    )
    VPN_REMOTE_ADD_SCRIPT = os.environ.get(
        "VPN_REMOTE_ADD_SCRIPT", "/usr/local/sbin/xray-add-client"
    ).strip()
    VPN_REMOTE_REMOVE_SCRIPT = os.environ.get(
        "VPN_REMOTE_REMOVE_SCRIPT", "/usr/local/sbin/xray-remove-client"
    ).strip()
    VPN_REMOTE_BUILD_LINK_SCRIPT = os.environ.get(
        "VPN_REMOTE_BUILD_LINK_SCRIPT", "/usr/local/sbin/xray-build-vless-link"
    ).strip()
    VLESS_HOST = os.environ.get("VLESS_HOST", "").strip()
    VLESS_PORT = get_env_int("VLESS_PORT", 443)
    VLESS_PBK = os.environ.get("VLESS_PBK", "").strip()
    VLESS_SNI = os.environ.get("VLESS_SNI", "").strip()
    VLESS_SID = os.environ.get("VLESS_SID", "").strip()
    VLESS_FP = os.environ.get("VLESS_FP", "chrome").strip()
    VLESS_FLOW = os.environ.get("VLESS_FLOW", "xtls-rprx-vision").strip()
    VLESS_TYPE = os.environ.get("VLESS_TYPE", "tcp").strip()
    VLESS_SECURITY = os.environ.get("VLESS_SECURITY", "reality").strip()
    VLESS_ENCRYPTION = os.environ.get("VLESS_ENCRYPTION", "none").strip()

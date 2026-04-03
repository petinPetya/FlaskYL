import json
import uuid
from datetime import datetime, timedelta

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from lowlands_vpn.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    balance = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    subscriptions = db.relationship(
        "Subscription", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )
    invoices = db.relationship(
        "Invoice", backref="user", lazy="dynamic", cascade="all, delete-orphan"
    )

    def __init__(self, email, password=None, **kwargs):
        self.email = email
        if password:
            self.set_password(password)
        super().__init__(**kwargs)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def can_afford(self, amount_cents):
        return self.balance >= amount_cents

    def charge(self, amount_cents):
        if not self.can_afford(amount_cents):
            raise ValueError(
                f"Insufficient funds. Need {amount_cents}, have {self.balance}"
            )
        self.balance -= amount_cents

    def deposit(self, amount_cents):
        self.balance += amount_cents

    def __repr__(self):
        return f"User {self.email}"


class Tariff(db.Model):
    __tablename__ = "tariffs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default="")
    price_cents = db.Column(db.Integer, nullable=False)
    days_valid = db.Column(db.Integer, nullable=False)
    traffic_limit_bytes = db.Column(db.BigInteger, nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_popular = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subscriptions = db.relationship("Subscription", backref="tariff", lazy="dynamic")

    def is_unlimited_traffic(self):
        return self.traffic_limit_bytes is None or self.traffic_limit_bytes == 0

    def is_unlimited_time(self):
        return self.days_valid == 0

    def __repr__(self):
        return f"Tariff {self.name}"


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tariff_id = db.Column(
        db.String(36), db.ForeignKey("tariffs.id", ondelete="RESTRICT"), nullable=False
    )

    starts_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

    used_traffic_bytes = db.Column(db.BigInteger, default=0, nullable=False)
    traffic_limit_bytes = db.Column(db.BigInteger, nullable=True)

    status = db.Column(db.String(32), default="active", nullable=False, index=True)

    config_code = db.Column(db.String(100), unique=True, nullable=True)
    public_key = db.Column(db.String(128), nullable=True)
    private_key = db.Column(db.String(128), nullable=True)
    vpn_ip = db.Column(db.String(15), nullable=True)

    auto_renew = db.Column(db.Boolean, default=False)
    last_renewed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    invoices = db.relationship("Invoice", backref="subscription", lazy="dynamic")

    def __init__(self, **kwargs):
        if "traffic_limit_bytes" not in kwargs and "tariff" in kwargs:
            kwargs["traffic_limit_bytes"] = kwargs["tariff"].traffic_limit_bytes
        super().__init__(**kwargs)

    def is_expired(self):
        return datetime.utcnow() > self.expires_at

    def is_traffic_exceeded(self):
        if self.traffic_limit_bytes is None:
            return False
        return self.used_traffic_bytes >= self.traffic_limit_bytes

    def is_active(self):
        return (
            self.status == "active"
            and not self.is_expired()
            and not self.is_traffic_exceeded()
        )

    def get_remaining_traffic(self):
        if self.traffic_limit_bytes is None:
            return None
        remaining = self.traffic_limit_bytes - self.used_traffic_bytes
        return max(0, remaining)

    def get_remaining_days(self):
        if self.is_expired():
            return 0
        delta = self.expires_at - datetime.utcnow()
        return max(0, delta.days)

    def get_usage_percent(self):
        if self.traffic_limit_bytes is None or self.traffic_limit_bytes == 0:
            return 0
        percent = (self.used_traffic_bytes / self.traffic_limit_bytes) * 100
        return min(100, percent)

    def renew(self, tariff=None):
        if not tariff:
            tariff = db.session.get(Tariff, self.tariff_id)

        now = datetime.utcnow()

        if self.is_expired():
            self.starts_at = now
            self.expires_at = now + timedelta(days=tariff.days_valid)
        else:
            self.expires_at = self.expires_at + timedelta(days=tariff.days_valid)

        self.tariff_id = tariff.id
        self.traffic_limit_bytes = tariff.traffic_limit_bytes
        self.status = "active"
        self.last_renewed_at = now

    def add_traffic(self, bytes_added):
        self.used_traffic_bytes += bytes_added

        if (
            self.traffic_limit_bytes
            and self.used_traffic_bytes >= self.traffic_limit_bytes
        ):
            self.status = "traffic_exceeded"

    def __repr__(self):
        return f"Subscription {self.id}"


class Invoice(db.Model):
    __tablename__ = "invoices"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_id = db.Column(
        db.String(36),
        db.ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )

    amount_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(32), default="pending", nullable=False, index=True)
    type = db.Column(db.String(32), nullable=False)

    payment_system = db.Column(db.String(32), nullable=True)
    payment_system_id = db.Column(db.String(255), nullable=True, index=True)
    payment_url = db.Column(db.Text, nullable=True)

    description = db.Column(db.Text, default="")
    _metadata = db.Column(db.Text, default="{}", name="metadata_json")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime, nullable=True)

    def get_metadata(self):
        return json.loads(self._metadata) if self._metadata else {}

    def set_metadata(self, data):
        self._metadata = json.dumps(data)

    def mark_as_paid(self, payment_system_id=None):
        self.status = "paid"
        self.paid_at = datetime.utcnow()
        if payment_system_id:
            self.payment_system_id = payment_system_id

    def mark_as_failed(self, reason=None):
        self.status = "failed"
        if reason:
            metadata = self.get_metadata()
            metadata["failure_reason"] = reason
            self.set_metadata(metadata)

    def __repr__(self):
        return f"Invoice {self.id}"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, user_id)

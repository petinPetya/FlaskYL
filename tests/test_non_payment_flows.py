import json
from datetime import timedelta

from lowlands_vpn.extensions import db
from lowlands_vpn.models import Device, Invoice, Subscription, Tariff, User, utc_now


def register_user(client, email: str, password: str = "strong-pass-123"):
    return client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=True,
    )


def login_user(client, email: str, password: str = "strong-pass-123"):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def logout_user(client):
    return client.post("/logout", data={}, follow_redirects=True)


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def enable_vpn_ssh_config(app):
    app.config.update(
        {
            "VPN_AUTO_PROVISION": True,
            "VPN_SSH_HOST": "vpn.example.com",
            "VPN_SSH_PORT": 22,
            "VPN_SSH_USER": "deployer",
            "VPN_SSH_KEY_PATH": "",
            "VPN_SSH_CONNECT_TIMEOUT": 5,
            "VPN_SSH_STRICT_HOST_KEY_CHECKING": True,
            "VPN_REMOTE_ADD_SCRIPT": "/usr/local/sbin/xray-add-client",
            "VPN_REMOTE_REMOVE_SCRIPT": "/usr/local/sbin/xray-remove-client",
            "VPN_REMOTE_BUILD_LINK_SCRIPT": "/usr/local/sbin/xray-build-vless-link",
            "VPN_REMOTE_LIST_SCRIPT": "/usr/local/sbin/xray-list-clients",
            "VLESS_HOST": "",
            "VLESS_PBK": "",
            "VLESS_SNI": "",
            "VLESS_SID": "",
        }
    )


def test_registration_creates_account_without_subscription(app, client):
    response = register_user(client, "first@example.com")

    assert response.status_code == 200
    assert "можно выбрать тариф" in response.get_data(as_text=True)

    user = db.session.scalar(db.select(User).where(User.email == "first@example.com"))
    assert user is not None
    assert user.is_admin is True
    assert Subscription.query.count() == 0


def test_public_pages_and_auth_flow_work(app, client):
    index_response = client.get("/")
    login_page = client.get("/login")
    register_page = client.get("/register")
    dashboard_redirect = client.get("/dashboard", follow_redirects=False)

    assert index_response.status_code == 200
    assert login_page.status_code == 200
    assert register_page.status_code == 200
    assert dashboard_redirect.status_code == 302
    assert "/login" in dashboard_redirect.headers["Location"]

    register_user(client, "flow@example.com")
    logout_response = logout_user(client)
    failed_login = client.post(
        "/login",
        data={"email": "flow@example.com", "password": "wrong-pass"},
        follow_redirects=True,
    )
    success_login = login_user(client, "flow@example.com")

    assert logout_response.status_code == 200
    assert "вышли из аккаунта" in logout_response.get_data(as_text=True)
    assert "Неверный email или пароль." in failed_login.get_data(as_text=True)
    assert "успешно вошли" in success_login.get_data(as_text=True)


def test_user_can_create_pending_subscription_request(app, client):
    register_user(client, "client@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))

    response = client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Запрос на тариф создан" in response.get_data(as_text=True)

    invoice = db.session.scalar(db.select(Invoice))
    assert invoice is not None
    assert invoice.status == "pending"
    assert invoice.type == "subscription_request"
    assert invoice.get_requested_tariff_id() == starter.id
    assert Subscription.query.count() == 0


def test_admin_can_approve_subscription_request(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    family = db.session.scalar(db.select(Tariff).where(Tariff.name == "Family"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": family.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    response = client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Запрос подтвержден" in response.get_data(as_text=True)

    approved_invoice = db.session.get(Invoice, invoice.id)
    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == approved_invoice.user_id)
    )
    assert approved_invoice.status == "paid"
    assert subscription is not None
    assert subscription.status == "active"
    assert subscription.tariff_id == family.id


def test_non_admin_cannot_access_admin_routes(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)
    register_user(client, "user@example.com")

    response = client.get("/admin", follow_redirects=True)

    assert response.status_code == 200
    assert "только администраторам" in response.get_data(as_text=True)


def test_admin_user_pages_render(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)
    register_user(client, "user@example.com")
    user = db.session.scalar(db.select(User).where(User.email == "user@example.com"))
    logout_user(client)

    login_user(client, "admin@example.com")
    users_page = client.get("/admin/users", follow_redirects=True)
    detail_page = client.get(f"/admin/users/{user.id}", follow_redirects=True)

    assert users_page.status_code == 200
    assert "user@example.com" in users_page.get_data(as_text=True)
    assert detail_page.status_code == 200
    assert "Карточка пользователя" in detail_page.get_data(as_text=True)


def test_admin_can_toggle_user_role_active_and_balance(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)
    register_user(client, "user@example.com")
    user = db.session.scalar(db.select(User).where(User.email == "user@example.com"))
    logout_user(client)

    login_user(client, "admin@example.com")
    role_response = client.post(
        f"/admin/users/{user.id}/toggle-admin",
        data={},
        follow_redirects=True,
    )
    status_response = client.post(
        f"/admin/users/{user.id}/toggle-active",
        data={},
        follow_redirects=True,
    )
    deposit_response = client.post(
        f"/admin/users/{user.id}/deposit",
        data={"deposit-amount_rub": 500},
        follow_redirects=True,
    )
    charge_response = client.post(
        f"/admin/users/{user.id}/charge",
        data={"charge-amount_rub": 125},
        follow_redirects=True,
    )

    updated_user = db.session.get(User, user.id)

    assert role_response.status_code == 200
    assert "Роль пользователя обновлена." in role_response.get_data(as_text=True)
    assert status_response.status_code == 200
    assert "Статус пользователя обновлен." in status_response.get_data(as_text=True)
    assert deposit_response.status_code == 200
    assert "Баланс пополнен." in deposit_response.get_data(as_text=True)
    assert charge_response.status_code == 200
    assert "Средства списаны с баланса." in charge_response.get_data(as_text=True)
    assert updated_user.is_admin is True
    assert updated_user.is_active is False
    assert updated_user.balance == 37500


def test_admin_subscription_is_lifetime_by_default(app, client):
    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))

    response = client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == invoice.user_id)
    )

    assert response.status_code == 200
    assert subscription is not None
    assert subscription.is_lifetime is True


def test_admin_can_delete_paid_subscription_request_note(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    response = client.post(
        f"/admin/invoices/{invoice.id}/delete",
        data={},
        follow_redirects=True,
    )

    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == invoice.user_id)
    )
    deleted_invoice = db.session.get(Invoice, invoice.id)

    assert response.status_code == 200
    assert "Запись об оплаченной заявке удалена" in response.get_data(as_text=True)
    assert deleted_invoice is None
    assert subscription is not None


def test_admin_can_delete_revoked_subscription_note(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    family = db.session.scalar(db.select(Tariff).where(Tariff.name == "Family"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": family.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)

    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == invoice.user_id)
    )
    subscription.status = "revoked"
    db.session.commit()

    response = client.post(
        f"/admin/subscriptions/{subscription.id}/delete",
        data={},
        follow_redirects=True,
    )

    deleted_subscription = db.session.get(Subscription, subscription.id)
    updated_invoice = db.session.get(Invoice, invoice.id)

    assert response.status_code == 200
    assert "Отозванная подписка удалена из истории" in response.get_data(as_text=True)
    assert deleted_subscription is None
    assert updated_invoice is not None
    assert updated_invoice.subscription_id is None


def test_admin_can_cancel_pending_subscription_request(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    response = client.post(
        f"/admin/invoices/{invoice.id}/cancel",
        data={},
        follow_redirects=True,
    )

    updated_invoice = db.session.get(Invoice, invoice.id)

    assert response.status_code == 200
    assert "Запрос отменен." in response.get_data(as_text=True)
    assert updated_invoice.status == "cancelled"


def test_regular_user_subscription_expiry_cannot_be_managed_manually(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == invoice.user_id)
    )
    future_value = (utc_now() + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M")

    response = client.post(
        f"/admin/subscriptions/{subscription.id}/expiry",
        data={
            f"subscription-{subscription.id}-is_lifetime": "y",
            f"subscription-{subscription.id}-expires_at": future_value,
        },
        follow_redirects=True,
    )

    updated_subscription = db.session.get(Subscription, subscription.id)

    assert response.status_code == 200
    assert "только для подписок администратора" in response.get_data(as_text=True)
    assert updated_subscription.is_lifetime is False


def test_admin_can_set_manual_expiration_for_admin_subscription(app, client):
    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == invoice.user_id)
    )
    future_value = (utc_now() + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")

    response = client.post(
        f"/admin/subscriptions/{subscription.id}/expiry",
        data={
            f"subscription-{subscription.id}-is_lifetime": "",
            f"subscription-{subscription.id}-expires_at": future_value,
        },
        follow_redirects=True,
    )

    updated_subscription = db.session.get(Subscription, subscription.id)

    assert response.status_code == 200
    assert "Срок подписки обновлён." in response.get_data(as_text=True)
    assert updated_subscription.is_lifetime is False
    assert updated_subscription.expires_at.strftime("%Y-%m-%dT%H:%M") == future_value


def test_dashboard_shows_lifetime_expiration_for_admin(app, client):
    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    response = client.get("/dashboard", follow_redirects=True)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Бессрочно" in page
    assert "Без автоматического истечения" in page


def test_device_limit_is_enforced_and_can_be_reused_after_revoke(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    first_response = client.post(
        "/devices",
        data={"name": "Work Laptop", "platform": "windows"},
        follow_redirects=True,
    )
    second_response = client.post(
        "/devices",
        data={"name": "Second Laptop", "platform": "linux"},
        follow_redirects=True,
    )

    device = db.session.scalar(db.select(Device).where(Device.name == "Work Laptop"))
    revoke_response = client.post(
        f"/devices/{device.id}/revoke",
        data={},
        follow_redirects=True,
    )
    third_response = client.post(
        "/devices",
        data={"name": "Replacement Laptop", "platform": "linux"},
        follow_redirects=True,
    )

    assert "Устройство добавлено" in first_response.get_data(as_text=True)
    assert "Лимит устройств" in second_response.get_data(as_text=True)
    assert "Устройство отозвано" in revoke_response.get_data(as_text=True)
    assert "Устройство добавлено" in third_response.get_data(as_text=True)

    active_devices = Device.query.filter(Device.status != "revoked").count()
    assert active_devices == 1


def test_admin_is_not_limited_by_tariff_device_cap(app, client):
    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))

    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))

    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    first_response = client.post(
        "/devices",
        data={"name": "Admin Laptop", "platform": "windows"},
        follow_redirects=True,
    )
    second_response = client.post(
        "/devices",
        data={"name": "Admin Phone", "platform": "android"},
        follow_redirects=True,
    )

    active_devices = Device.query.filter(Device.status != "revoked").count()

    assert "Устройство добавлено" in first_response.get_data(as_text=True)
    assert "Устройство добавлено" in second_response.get_data(as_text=True)
    assert active_devices == 2


def test_admin_can_update_device_provisioning_state(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    family = db.session.scalar(db.select(Tariff).where(Tariff.name == "Family"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": family.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    client.post(
        "/devices",
        data={"name": "Phone", "platform": "android"},
        follow_redirects=True,
    )
    device = db.session.scalar(db.select(Device).where(Device.name == "Phone"))
    logout_user(client)

    login_user(client, "admin@example.com")
    response = client.post(
        f"/admin/devices/{device.id}/update",
        data={
            f"device-{device.id}-status": "active",
            f"device-{device.id}-provisioning_state": "ready",
            f"device-{device.id}-assigned_ip": "10.0.0.2",
            f"device-{device.id}-last_error": "",
        },
        follow_redirects=True,
    )

    updated_device = db.session.get(Device, device.id)
    assert response.status_code == 200
    assert "Параметры устройства обновлены" in response.get_data(as_text=True)
    assert updated_device.status == "active"
    assert updated_device.provisioning_state == "ready"
    assert updated_device.assigned_ip == "10.0.0.2"


def test_admin_dashboard_shows_server_vless_clients(app, client, monkeypatch):
    enable_vpn_ssh_config(app)

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        if "xray-list-clients" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {
                        "status": "ok",
                        "stats_enabled": False,
                        "clients": [
                            {
                                "uuid": "server-uuid-1",
                                "email": "user1@xray",
                                "name": "user1@xray",
                                "flow": "xtls-rprx-vision",
                                "link": "vless://server-uuid-1@test:443",
                                "stats": {
                                    "available": False,
                                    "uplink_bytes": None,
                                    "downlink_bytes": None,
                                    "total_bytes": None,
                                },
                            }
                        ],
                    }
                )
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    register_user(client, "admin@example.com")
    response = client.get("/admin", follow_redirects=True)

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "VLESS ссылки на сервере" in page
    assert "user1@xray" in page
    assert "vless://server-uuid-1@test:443" in page


def test_admin_can_delete_server_vless_client_and_sync_local_device(
    app, client, monkeypatch
):
    enable_vpn_ssh_config(app)

    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )
    admin_user = db.session.scalar(
        db.select(User).where(User.email == "admin@example.com")
    )

    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == admin_user.id)
    )
    device = Device(
        subscription_id=subscription.id,
        name="Server Device",
        platform="windows",
        status="active",
        provisioning_state="ready",
        vpn_uuid="server-uuid-1",
        vpn_email="user1@xray",
        vpn_link="vless://server-uuid-1@test:443",
    )
    db.session.add(device)
    db.session.commit()

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        if "xray-remove-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps({"status": "ok", "removed_count": 1})
            )
        if "xray-list-clients" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {
                        "status": "ok",
                        "stats_enabled": False,
                        "clients": [],
                    }
                )
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    response = client.post(
        "/admin/vpn/clients/server-uuid-1/delete",
        data={},
        follow_redirects=True,
    )

    updated_device = db.session.get(Device, device.id)
    assert response.status_code == 200
    assert "VLESS-ссылка удалена с сервера." in response.get_data(as_text=True)
    assert updated_device.status == "revoked"
    assert updated_device.provisioning_state == "revoked"
    assert updated_device.vpn_link is None


def test_device_is_provisioned_via_vpn_server_when_configured(app, client, monkeypatch):
    enable_vpn_ssh_config(app)
    recorded_commands = []

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        recorded_commands.append(remote_command)
        if "xray-add-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {
                        "status": "ok",
                        "uuid": "1430dff8-73ef-44bf-a9ce-09c3ef9b638b",
                        "email": "device-placeholder@xray",
                        "link": "vless://1430dff8-73ef-44bf-a9ce-09c3ef9b638b@147.45.224.143:443?type=tcp#Work%20Laptop",
                    }
                )
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    response = client.post(
        "/devices",
        data={"name": "Work Laptop", "platform": "windows"},
        follow_redirects=True,
    )

    device = db.session.scalar(db.select(Device).where(Device.name == "Work Laptop"))
    assert response.status_code == 200
    assert "VPN-ссылка готова" in response.get_data(as_text=True)
    assert device is not None
    assert device.status == "active"
    assert device.provisioning_state == "ready"
    assert device.vpn_uuid is not None
    assert device.vpn_email == f"device-{device.id}@xray"
    assert device.vpn_link.startswith("vless://")
    assert not any("xray-build-vless-link" in command for command in recorded_commands)


def test_device_provision_failure_is_saved_on_device(app, client, monkeypatch):
    enable_vpn_ssh_config(app)

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        if "xray-add-client" in remote_command:
            return FakeCompletedProcess(returncode=1, stderr="ssh timeout")
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    response = client.post(
        "/devices",
        data={"name": "Broken Laptop", "platform": "windows"},
        follow_redirects=True,
    )

    device = db.session.scalar(db.select(Device).where(Device.name == "Broken Laptop"))
    assert response.status_code == 200
    assert "выдача VPN завершилась ошибкой" in response.get_data(as_text=True)
    assert device is not None
    assert device.status == "pending"
    assert device.provisioning_state == "failed"
    assert "ssh timeout" in device.last_error


def test_user_revoke_calls_vpn_remove_script(app, client, monkeypatch):
    enable_vpn_ssh_config(app)
    recorded_commands = []

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        recorded_commands.append(remote_command)
        if "xray-add-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps({"status": "ok", "link": "vless://device-link"})
            )
        if "xray-remove-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps({"status": "ok", "removed_count": 1})
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    client.post(
        "/devices",
        data={"name": "Phone", "platform": "android"},
        follow_redirects=True,
    )
    device = db.session.scalar(db.select(Device).where(Device.name == "Phone"))

    response = client.post(
        f"/devices/{device.id}/revoke",
        data={},
        follow_redirects=True,
    )

    updated_device = db.session.get(Device, device.id)
    assert response.status_code == 200
    assert "Устройство отозвано" in response.get_data(as_text=True)
    assert any("xray-remove-client" in command for command in recorded_commands)
    assert updated_device.status == "revoked"
    assert updated_device.provisioning_state == "revoked"
    assert updated_device.vpn_link is None


def test_admin_can_delete_single_revoked_device_record(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    family = db.session.scalar(db.select(Tariff).where(Tariff.name == "Family"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": family.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    client.post(
        "/devices",
        data={"name": "Old Phone", "platform": "android"},
        follow_redirects=True,
    )
    device = db.session.scalar(db.select(Device).where(Device.name == "Old Phone"))
    client.post(f"/devices/{device.id}/revoke", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "admin@example.com")
    response = client.post(
        f"/admin/devices/{device.id}/delete",
        data={},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Запись об отозванном устройстве удалена" in response.get_data(as_text=True)
    assert db.session.get(Device, device.id) is None


def test_admin_can_delete_all_revoked_device_records(app, client):
    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    family = db.session.scalar(db.select(Tariff).where(Tariff.name == "Family"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": family.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    client.post(
        "/devices",
        data={"name": "Old Phone", "platform": "android"},
        follow_redirects=True,
    )
    client.post(
        "/devices",
        data={"name": "Old Laptop", "platform": "windows"},
        follow_redirects=True,
    )
    client.post(
        "/devices",
        data={"name": "Current Tablet", "platform": "ios"},
        follow_redirects=True,
    )

    old_phone = db.session.scalar(db.select(Device).where(Device.name == "Old Phone"))
    old_laptop = db.session.scalar(db.select(Device).where(Device.name == "Old Laptop"))
    current_tablet = db.session.scalar(
        db.select(Device).where(Device.name == "Current Tablet")
    )

    client.post(f"/devices/{old_phone.id}/revoke", data={}, follow_redirects=True)
    client.post(f"/devices/{old_laptop.id}/revoke", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "admin@example.com")
    response = client.post(
        f"/admin/users/{invoice.user_id}/devices/delete-revoked",
        data={},
        follow_redirects=True,
    )

    remaining_devices = (
        Device.query.join(Subscription)
        .filter(Subscription.user_id == invoice.user_id)
        .order_by(Device.name.asc())
        .all()
    )

    assert response.status_code == 200
    assert "Удалено отозванных устройств: 2." in response.get_data(as_text=True)
    assert db.session.get(Device, old_phone.id) is None
    assert db.session.get(Device, old_laptop.id) is None
    assert db.session.get(Device, current_tablet.id) is not None
    assert [device.name for device in remaining_devices] == ["Current Tablet"]


def test_admin_can_retry_device_provisioning(app, client, monkeypatch):
    app.config.update({"VPN_AUTO_PROVISION": False})

    register_user(client, "admin@example.com")
    logout_user(client)

    register_user(client, "user@example.com")
    family = db.session.scalar(db.select(Tariff).where(Tariff.name == "Family"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": family.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    logout_user(client)

    login_user(client, "admin@example.com")
    client.post(f"/admin/invoices/{invoice.id}/approve", data={}, follow_redirects=True)
    logout_user(client)

    login_user(client, "user@example.com")
    client.post(
        "/devices",
        data={"name": "Tablet", "platform": "android"},
        follow_redirects=True,
    )
    device = db.session.scalar(db.select(Device).where(Device.name == "Tablet"))
    logout_user(client)

    enable_vpn_ssh_config(app)

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        if "xray-add-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps({"status": "ok", "link": "vless://retry-link"})
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    login_user(client, "admin@example.com")
    response = client.post(
        f"/admin/devices/{device.id}/provision",
        data={},
        follow_redirects=True,
    )

    updated_device = db.session.get(Device, device.id)
    assert response.status_code == 200
    assert "VPN для устройства подготовлен" in response.get_data(as_text=True)
    assert updated_device.status == "active"
    assert updated_device.provisioning_state == "ready"
    assert updated_device.vpn_link == "vless://retry-link"


def test_admin_dashboard_auto_revokes_expired_subscription_devices(
    app, client, monkeypatch
):
    enable_vpn_ssh_config(app)
    recorded_commands = []

    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    admin_user = db.session.scalar(
        db.select(User).where(User.email == "admin@example.com")
    )
    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == admin_user.id)
    )
    subscription.is_lifetime = False
    subscription.status = "active"
    subscription.expires_at = utc_now() - timedelta(days=1)
    device = Device(
        subscription_id=subscription.id,
        name="Expired Laptop",
        platform="windows",
        status="active",
        provisioning_state="ready",
        vpn_uuid="expired-uuid-1",
        vpn_email="expired-1@xray",
        vpn_link="vless://expired-uuid-1@test:443",
    )
    db.session.add(device)
    db.session.commit()

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        recorded_commands.append(remote_command)
        if "xray-remove-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps({"status": "ok", "removed_count": 1})
            )
        if "xray-list-clients" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {"status": "ok", "stats_enabled": False, "clients": []}
                )
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    response = client.get("/admin", follow_redirects=True)

    updated_subscription = db.session.get(Subscription, subscription.id)
    updated_device = db.session.get(Device, device.id)

    assert response.status_code == 200
    assert updated_subscription.status == "expired"
    assert updated_device.status == "revoked"
    assert updated_device.provisioning_state == "revoked"
    assert "истекла" in updated_device.last_error
    assert any("xray-remove-client" in command for command in recorded_commands)


def test_admin_sync_vpn_marks_missing_server_clients_as_failed(
    app, client, monkeypatch
):
    enable_vpn_ssh_config(app)

    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    admin_user = db.session.scalar(
        db.select(User).where(User.email == "admin@example.com")
    )
    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == admin_user.id)
    )
    device = Device(
        subscription_id=subscription.id,
        name="Missing On Server",
        platform="windows",
        status="active",
        provisioning_state="ready",
        vpn_uuid="missing-server-uuid",
        vpn_email="missing-server@xray",
        vpn_link="vless://missing-server-uuid@test:443",
    )
    db.session.add(device)
    db.session.commit()

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        if "xray-list-clients" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {"status": "ok", "stats_enabled": False, "clients": []}
                )
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    response = client.post("/admin/vpn/sync", data={}, follow_redirects=True)

    updated_device = db.session.get(Device, device.id)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Сверка с Xray завершена." in page
    assert "Отсутствуют на сервере: 1." in page
    assert updated_device.status == "pending"
    assert updated_device.provisioning_state == "failed"
    assert "отсутствует на сервере Xray" in updated_device.last_error


def test_admin_sync_vpn_removes_stale_server_clients_for_revoked_devices(
    app, client, monkeypatch
):
    enable_vpn_ssh_config(app)
    recorded_commands = []

    register_user(client, "admin@example.com")
    starter = db.session.scalar(db.select(Tariff).where(Tariff.name == "Starter"))
    client.post(
        "/subscriptions/request",
        data={"tariff_id": starter.id},
        follow_redirects=True,
    )
    invoice = db.session.scalar(db.select(Invoice))
    client.post(
        f"/admin/invoices/{invoice.id}/approve",
        data={},
        follow_redirects=True,
    )

    admin_user = db.session.scalar(
        db.select(User).where(User.email == "admin@example.com")
    )
    subscription = db.session.scalar(
        db.select(Subscription).where(Subscription.user_id == admin_user.id)
    )
    device = Device(
        subscription_id=subscription.id,
        name="Revoked Server Device",
        platform="windows",
        status="revoked",
        provisioning_state="revoked",
        vpn_uuid="revoked-server-uuid",
        vpn_email="revoked-server@xray",
        vpn_link="vless://revoked-server-uuid@test:443",
    )
    db.session.add(device)
    db.session.commit()

    def fake_run(command, capture_output, check, text):
        remote_command = command[-1]
        recorded_commands.append(remote_command)
        if "xray-list-clients" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps(
                    {
                        "status": "ok",
                        "stats_enabled": False,
                        "clients": [
                            {
                                "uuid": "revoked-server-uuid",
                                "email": "revoked-server@xray",
                                "name": "revoked-server@xray",
                                "flow": "xtls-rprx-vision",
                                "link": "vless://revoked-server-uuid@test:443",
                                "stats": {
                                    "available": False,
                                    "uplink_bytes": None,
                                    "downlink_bytes": None,
                                    "total_bytes": None,
                                },
                            }
                        ],
                    }
                )
            )
        if "xray-remove-client" in remote_command:
            return FakeCompletedProcess(
                stdout=json.dumps({"status": "ok", "removed_count": 1})
            )
        raise AssertionError(f"Unexpected command: {remote_command}")

    monkeypatch.setattr("lowlands_vpn.vpn.subprocess.run", fake_run)

    response = client.post("/admin/vpn/sync", data={}, follow_redirects=True)

    updated_device = db.session.get(Device, device.id)
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Очищено серверных хвостов: 1." in page
    assert updated_device.vpn_link is None
    assert any("xray-remove-client" in command for command in recorded_commands)

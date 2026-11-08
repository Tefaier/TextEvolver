import datetime as dt
import hashlib
import json
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import cbor2
import pytest
from conftest import csrf_from
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn.helpers import bytes_to_base64url as b64

from text_evolver.db.models import (
    ApiToken,
    AuthFlow,
    BrowserSession,
    BrowserSessionFlash,
    GoogleIdentity,
    Passkey,
    UserAccount,
    UserPassword,
)
from text_evolver.web import sessions, utils
from text_evolver.web.routines import google
from text_evolver.web.routines.password import verify_password


def headers(client, path="/login"):
    return {"X-CSRF-Token": csrf_from(client.get(path)), "Accept": "application/json"}


def browser_session(database, client):
    with Session(database) as db:
        return db.scalar(
            select(BrowserSession).where(
                BrowserSession.token_hash == utils.digest(client.cookies.get(sessions.COOKIE))
            )
        )


def expire_freshness(database, client):
    with Session(database) as db:
        row = db.scalar(
            select(BrowserSession).where(
                BrowserSession.token_hash == utils.digest(client.cookies.get(sessions.COOKIE))
            )
        )
        row.authenticated_at = utils.utcnow() - dt.timedelta(minutes=5)
        db.commit()


def test_cookie_is_opaque_and_fixed_seven_days(registered_client, database, monkeypatch):
    client = registered_client
    cookie = client.cookies.get(sessions.COOKIE)
    assert len(cookie) == 43
    row = browser_session(database, client)
    assert row.token_hash == hashlib.sha256(cookie.encode()).hexdigest()
    csrf = csrf_from(client.get("/account/settings"))
    assert csrf != cookie
    assert csrf != row.token_hash
    assert utils.utc(row.expires_at) - utils.utc(row.created_at) == dt.timedelta(days=7)
    expires = utils.utc(row.expires_at)
    monkeypatch.setattr(utils, "utcnow", lambda: expires - dt.timedelta(microseconds=1))
    assert client.get("/my_settings").status_code == 200
    assert browser_session(database, client).expires_at == row.expires_at
    monkeypatch.setattr(utils, "utcnow", lambda: expires)
    response = client.get("/my_settings", headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.cookies.get(sessions.COOKIE) is None


def test_signed_csrf_cookie_contains_only_token_and_cannot_authenticate(registered_client, app_settings):
    import base64

    from itsdangerous import TimestampSigner

    client = registered_client
    page = client.get("/account/settings")
    cookie = client.cookies.get(sessions.CSRF_COOKIE)
    encoded = TimestampSigner(app_settings.secret_key).unsign(cookie)
    assert json.loads(base64.b64decode(encoded)) == {"csrf_token": csrf_from(page)}
    with TestClient(client.app) as other:
        other.cookies.set(sessions.CSRF_COOKIE, cookie)
        assert other.get("/my_settings", headers={"Accept": "application/json"}).status_code == 401


def test_tampered_csrf_cookie_rejects_form_token(client):
    csrf = csrf_from(client.get("/login"))
    client.cookies.clear()
    client.cookies.set(sessions.CSRF_COOKIE, "invalid-signature")
    response = client.post(
        "/login",
        headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
        data={"username": "reader", "password": "correct horse"},
    )
    assert response.status_code == 403


def test_logout_revokes_copied_cookie(registered_client):
    client = registered_client
    copied = client.cookies.get(sessions.COOKIE)
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/my_settings"))})
    client.cookies.clear()
    client.cookies.set(sessions.COOKIE, copied)
    assert client.get("/my_settings", follow_redirects=False).status_code == 303


def test_reauthentication_rotates_without_extending_expiration(registered_client, database, monkeypatch):
    client = registered_client
    original = browser_session(database, client)
    original_csrf = csrf_from(client.get("/account/settings"))
    copied = client.cookies.get(sessions.COOKIE)
    with Session(database) as db:
        db.add(
            AuthFlow(
                browser_session_token_hash=original.token_hash,
                purpose="register",
                token_hash="pending-flow",
                user_id=original.user_id,
                data="{}",
                expires_at=utils.utcnow() + dt.timedelta(minutes=5),
            )
        )
        db.commit()
    expire_freshness(database, client)
    response = client.post(
        "/account/settings",
        data={"action": "username", "username": "renamed"},
        headers=headers(client, "/account/settings"),
    )
    assert response.status_code == 428
    assert response.json()["redirect"] == "/account/reauthenticate"
    response = client.post(
        "/account/reauthenticate",
        data={"password": "correct horse"},
        headers=headers(client, "/account/reauthenticate"),
    )
    assert response.status_code == 200
    assert client.cookies.get(sessions.COOKIE) != copied
    current = browser_session(database, client)
    assert current.expires_at == original.expires_at
    assert current.created_at == original.created_at
    assert current.token_hash != original.token_hash
    assert csrf_from(client.get("/account/settings")) != original_csrf
    with Session(database) as db:
        assert db.get(BrowserSession, original.token_hash) is None
        flow = db.scalar(select(AuthFlow).where(AuthFlow.token_hash == "pending-flow"))
        assert flow.browser_session_token_hash == current.token_hash
    client.post(
        "/account/settings",
        data={"action": "username", "username": "renamed"},
        headers=headers(client, "/account/settings"),
    )
    with Session(database) as db:
        assert db.get(UserAccount, current.user_id).username == "renamed"
    client.cookies.clear()
    client.cookies.set(sessions.COOKIE, copied)
    assert client.get("/my_settings", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("invalidation", ["logout", "reauthenticate"])
def test_stale_response_cannot_resurrect_or_modify_session(registered_client, database, invalidation):
    client = registered_client
    ready = threading.Event()
    release = threading.Event()
    copied = client.cookies.get(sessions.COOKIE)

    def slow(request: Request):
        request.session["csrf_token"] = "stale"
        ready.set()
        assert release.wait(10)
        return {"ok": True}

    client.app.state.web_app.add_api_route("/test-slow", slow)
    with TestClient(client.app) as other, ThreadPoolExecutor() as pool:
        other.cookies.update(client.cookies)
        future = pool.submit(other.get, "/test-slow")
        assert ready.wait(10)
        if invalidation == "logout":
            client.post("/logout", data={"csrf_token": csrf_from(client.get("/my_settings"))}, follow_redirects=False)
        else:
            assert (
                client.post(
                    "/account/reauthenticate",
                    headers=headers(client, "/account/reauthenticate"),
                    data={"password": "correct horse"},
                ).status_code
                == 200
            )
        release.set()
        response = future.result(timeout=10)
        assert not any(value.startswith(sessions.COOKIE + "=") for value in response.headers.get_list("set-cookie"))
    with Session(database) as db:
        row = db.scalar(select(BrowserSession).where(BrowserSession.token_hash == utils.digest(copied)))
        if invalidation == "logout":
            assert row.revoked_at is not None
        else:
            assert row is None
            current = db.get(BrowserSession, utils.digest(client.cookies.get(sessions.COOKIE)))
            assert current.revoked_at is None


def test_normalized_flash_messages_are_bounded_and_consumed_once(registered_client, database):
    from text_evolver.web.routines.flashes import flash

    client = registered_client

    async def queue(request: Request):
        for index in range(25):
            flash(request, f"Queued message {index}", "success")
        return {"ok": True}

    client.app.state.web_app.add_api_route("/test-flashes", queue, methods=["POST"])
    assert client.post("/test-flashes", headers=headers(client, "/my_settings")).status_code == 200
    with Session(database) as db:
        messages = list(db.scalars(select(BrowserSessionFlash.message).order_by(BrowserSessionFlash.id)))
        assert messages == [f"Queued message {index}" for index in range(5, 25)]
    page = client.get("/my_settings")
    assert "Queued message 24" in page.text
    assert "Queued message 24" not in client.get("/my_settings").text
    with Session(database) as db:
        assert db.scalar(select(BrowserSessionFlash.id)) is None


def test_session_flash_references_follow_hash_rotation_and_deletion(database):
    now = utils.utcnow()
    with Session(database) as db:
        row = BrowserSession(token_hash="old-hash", created_at=now, expires_at=now + dt.timedelta(days=7))
        db.add(row)
        db.flush()
        db.add(BrowserSessionFlash(browser_session_token_hash=row.token_hash, category="success", message="Message"))
        db.commit()
        row.token_hash = "new-hash"
        db.commit()
        assert db.scalar(select(BrowserSessionFlash.browser_session_token_hash)) == "new-hash"
        from sqlalchemy import delete

        db.execute(delete(BrowserSession).where(BrowserSession.token_hash == "new-hash"))
        db.commit()
        assert db.scalar(select(BrowserSessionFlash.id)) is None


def test_locale_logo_errors_and_persistence(client, database):
    page = client.get("/register", headers={"Accept-Language": "de; q=0.9,ru; q=0.8,en;q=0.5"})
    assert '<html lang="ru">' in page.text
    assert "Эволюция текста" in page.text
    response = client.post(
        "/register",
        headers={"Accept-Language": "ru", "Accept": "application/json"},
        data={"csrf_token": csrf_from(page), "username": "russian", "password": "short", "confirm": "short"},
    )
    assert "Пароль должен" in response.json()["message"]
    response = client.post(
        "/register",
        headers={"Accept-Language": "ru"},
        data={
            "csrf_token": csrf_from(page),
            "username": "russian",
            "password": "valid password",
            "confirm": "valid password",
        },
    )
    assert "Ваше рабочее пространство" in response.text
    assert '<html lang="en">' in client.get("/account/settings", headers={"Accept-Language": "en"}).text
    client.post("/language", data={"language": "ru"}, headers=headers(client, "/account/settings"))
    assert '<html lang="ru">' in client.get("/account/settings", headers={"Accept-Language": "en"}).text
    client.post("/language", data={"language": "default"}, headers=headers(client, "/account/settings"))
    assert '<html lang="en">' in client.get("/account/settings", headers={"Accept-Language": "en"}).text
    assert '<html lang="ru">' in client.get("/account/settings", headers={"Accept-Language": "ru"}).text
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/my_settings"))})
    assert "TEXT EVOLVER" in client.get("/login", headers={"Accept-Language": "en"}).text
    assert "/language" not in client.get("/register").text
    client.cookies.set("text_evolver_language", "ru")
    assert '<html lang="en">' in client.get("/login", headers={"Accept-Language": "en"}).text
    assert client.post("/language", data={"language": "ru"}, headers=headers(client)).status_code == 401


class Authenticator:
    """Small virtual authenticator producing real signed WebAuthn responses."""

    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.id = secrets.token_bytes(32)
        self.counter = 0
        self.handle = None

    def registration(self, public, *, origin="http://localhost:8000"):
        self.handle = public["user"]["id"]
        numbers = self.private.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        data = hashlib.sha256(public["rp"]["id"].encode()).digest() + b"\x45" + bytes(4)
        data += bytes(16) + len(self.id).to_bytes(2, "big") + self.id + cose
        client_data = json.dumps(
            {"type": "webauthn.create", "challenge": public["challenge"], "origin": origin}
        ).encode()
        return {
            "id": b64(self.id),
            "rawId": b64(self.id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "attestationObject": b64(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": data})),
                "transports": ["internal"],
            },
        }

    def assertion(self, public, *, origin="http://localhost:8000", rp_id="localhost", challenge=None):
        self.counter += 1
        client_data = json.dumps(
            {"type": "webauthn.get", "challenge": challenge or public["challenge"], "origin": origin}
        ).encode()
        auth_data = hashlib.sha256(rp_id.encode()).digest() + b"\x05" + self.counter.to_bytes(4, "big")
        signature = self.private.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64(self.id),
            "rawId": b64(self.id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64(client_data),
                "authenticatorData": b64(auth_data),
                "signature": b64(signature),
                "userHandle": self.handle,
            },
        }


def enroll(client):
    auth = Authenticator()
    h = headers(client, "/account/settings")
    options = client.post("/auth/webauthn/register/options", json={}, headers=h).json()
    credential = auth.registration(options["publicKey"])
    response = client.post(
        "/auth/webauthn/register/verify",
        json={"flow": options["flow"], "credential": credential, "name": "Laptop"},
        headers=h,
    )
    assert response.status_code == 200, response.text
    return auth


def assert_passkey(client, auth, operation="login", **kwargs):
    h = headers(client, "/login" if operation in {"login", "recover"} else "/account/reauthenticate")
    options = client.post(f"/auth/webauthn/{operation}/options", json={}, headers=h).json()
    payload = {"flow": options["flow"], "credential": auth.assertion(options["publicKey"], **kwargs)}
    response = client.post(f"/auth/webauthn/{operation}/verify", json=payload, headers=h)
    return response, payload, h


def sign_out(client):
    client.post("/logout", headers=headers(client, "/my_settings"))


def test_passkey_enrollment_login_reauthentication_and_replay(registered_client, database):
    client = registered_client
    auth = enroll(client)
    with Session(database) as db:
        user = db.scalar(select(UserAccount))
        assert isinstance(user.id, UUID)
        assert user.id.version == 4
        assert auth.handle == b64(user.id.bytes)
    sign_out(client)
    passkey_result = assert_passkey(client, auth)
    response = passkey_result[0]
    payload = passkey_result[1]
    h = passkey_result[2]
    assert response.status_code == 200, response.text
    assert client.get("/account/settings").status_code == 200
    expire_freshness(database, client)
    original = browser_session(database, client).expires_at
    assert assert_passkey(client, auth, "reauthenticate")[0].status_code == 200
    assert browser_session(database, client).expires_at == original
    assert client.post("/auth/webauthn/login/verify", json=payload, headers=h).status_code in {400, 403}


def test_passkey_rejects_another_account_uuid_handle(registered_client):
    auth = enroll(registered_client)
    sign_out(registered_client)
    auth.handle = b64(uuid4().bytes)
    assert assert_passkey(registered_client, auth)[0].status_code == 400


def test_passkey_counter_supports_unsigned_32_bit_values(registered_client, database):
    client = registered_client
    auth = enroll(client)
    auth.counter = 2**31 - 1
    sign_out(client)
    assert assert_passkey(client, auth)[0].status_code == 200
    with Session(database) as db:
        assert db.scalar(select(Passkey.sign_count)) == 2**31


@pytest.mark.parametrize("bad", [{"origin": "http://evil.example"}, {"rp_id": "evil.example"}, {"challenge": "wrong"}])
def test_passkey_rejects_wrong_origin_rp_and_challenge(registered_client, bad):
    auth = enroll(registered_client)
    sign_out(registered_client)
    assert assert_passkey(registered_client, auth, **bad)[0].status_code == 400


def test_webauthn_recovery_revokes_all_sessions(registered_client, database):
    client = registered_client
    auth = enroll(client)
    from text_evolver.api.tokens import issue_token

    with Session(database) as db:
        user_id = db.scalar(select(UserAccount.id))
        issue_token(db, user_id, "read")
        issue_token(db, user_id, "read_write")
        db.commit()
    copied = client.cookies.get(sessions.COOKIE)
    with TestClient(client.app) as other:
        other.cookies.set(sessions.COOKIE, copied)
        sign_out(client)
        # Establish a separate password session which recovery must revoke.
        other.cookies.clear()
        other.post(
            "/login",
            data={"csrf_token": csrf_from(other.get("/login")), "username": "reader", "password": "correct horse"},
        )
        passkey_result = assert_passkey(client, auth, "recover")
        response = passkey_result[0]
        assert response.status_code == 200, response.text
        assert client.get("/my_settings", follow_redirects=False).status_code == 303
        response = client.post(
            "/reset-password",
            data={"password": "replacement password", "confirm": "replacement password"},
            headers=headers(client, "/reset-password"),
        )
        assert response.status_code == 200, response.text
        assert other.get("/my_settings", headers={"Accept": "application/json"}).status_code == 401
        assert client.get("/my_settings", follow_redirects=False).status_code == 303
    with Session(database) as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        assert verify_password("replacement password", db.get(UserPassword, user.id))
        assert db.scalar(select(AuthFlow.id).where(AuthFlow.user_id == user.id)) is None
        assert db.scalar(select(ApiToken).where(ApiToken.user_id == user.id)) is None


@pytest.mark.parametrize("operation", ["invalid", "register-invalid", "recover-invalid"])
@pytest.mark.parametrize("endpoint", ["options", "verify"])
def test_webauthn_rejects_unknown_operations(client, operation, endpoint):
    response = client.post(f"/auth/webauthn/{operation}/{endpoint}", json={}, headers=headers(client))
    assert response.status_code == 404


def test_recovery_without_webauthn_and_csrf_is_rejected(client):
    assert client.post("/auth/webauthn/recover/options", json={}).status_code == 403
    h = headers(client)
    assert (
        client.post(
            "/reset-password", data={"password": "replacement password", "confirm": "replacement password"}, headers=h
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/auth/webauthn/recover/verify", json={"credential": {"id": b64(b"unknown")}, "flow": "wrong"}, headers=h
        ).status_code
        == 400
    )


def test_expired_and_wrong_purpose_challenges_fail(registered_client, database):
    client = registered_client
    auth = enroll(client)
    sign_out(client)
    h = headers(client)
    options = client.post("/auth/webauthn/recover/options", json={}, headers=h).json()
    payload = {"flow": options["flow"], "credential": auth.assertion(options["publicKey"])}
    assert client.post("/auth/webauthn/login/verify", json=payload, headers=h).status_code == 400
    with Session(database) as db:
        row = db.scalar(select(AuthFlow).where(AuthFlow.token_hash == utils.digest(options["flow"])))
        row.expires_at = utils.utcnow() - dt.timedelta(seconds=1)
        db.commit()
    assert client.post("/auth/webauthn/recover/verify", json=payload, headers=h).status_code == 400


def test_recovery_grant_is_expiring_and_single_use(registered_client, database):
    client = registered_client
    auth = enroll(client)
    sign_out(client)
    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    h = headers(client, "/reset-password")
    with Session(database) as db:
        grant = db.scalar(select(AuthFlow).where(AuthFlow.purpose == "reset"))
        grant.expires_at = utils.utcnow() - dt.timedelta(seconds=1)
        db.commit()
    body = {"password": "replacement password", "confirm": "replacement password"}
    assert client.post("/reset-password", headers=h, data=body).status_code == 400

    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    copied = client.cookies.get(sessions.COOKIE)
    h = headers(client, "/reset-password")
    assert client.post("/reset-password", headers=h, data=body).status_code == 200
    client.cookies.clear()
    client.cookies.set(sessions.COOKIE, copied)
    h = headers(client)
    assert client.post("/reset-password", headers=h, data=body).status_code == 400


def test_reauthentication_invalidates_existing_recovery_grant(registered_client):
    client = registered_client
    auth = enroll(client)
    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    assert client.get("/reset-password").status_code == 200
    response = client.post(
        "/account/reauthenticate",
        headers=headers(client, "/account/reauthenticate"),
        data={"password": "correct horse"},
    )
    assert response.status_code == 200
    assert client.get("/reset-password").status_code == 400


def test_repeated_recovery_replaces_the_sessions_grant(registered_client, database):
    client = registered_client
    auth = enroll(client)
    sign_out(client)
    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    with Session(database) as db:
        original = db.scalar(select(AuthFlow.token_hash).where(AuthFlow.purpose == "reset"))
    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    with Session(database) as db:
        grants = list(db.scalars(select(AuthFlow).where(AuthFlow.purpose == "reset")))
        assert len(grants) == 1
        assert grants[0].token_hash != original
    assert client.get("/reset-password").status_code == 200


@pytest.mark.parametrize("handle", [None, "x", 123, "different-user"])
def test_passkey_bad_user_handle_fails_safely(registered_client, handle):
    client = registered_client
    auth = enroll(client)
    sign_out(client)
    h = headers(client)
    options = client.post("/auth/webauthn/login/options", json={}, headers=h).json()
    credential = auth.assertion(options["publicKey"])
    credential["response"]["userHandle"] = handle
    assert (
        client.post(
            "/auth/webauthn/login/verify", headers=h, json={"flow": options["flow"], "credential": credential}
        ).status_code
        == 400
    )


def test_password_change_revokes_other_sessions_and_rotates_current(registered_client, database):
    client = registered_client
    with TestClient(client.app) as other:
        other.post("/login", headers=headers(other), data={"username": "reader", "password": "correct horse"})
        old = client.cookies.get(sessions.COOKIE)
        before = browser_session(database, client).expires_at
        body = {
            "action": "password",
            "current_password": "correct horse",
            "password": "new password value",
            "confirm": "new password value",
        }
        assert (
            client.post("/account/settings", headers=headers(client, "/account/settings"), data=body).status_code == 200
        )
        assert client.cookies.get(sessions.COOKIE) != old
        assert browser_session(database, client).expires_at == before
        assert other.get("/my_settings", headers={"Accept": "application/json"}).status_code == 401


def test_last_passkey_cannot_be_removed_without_alternative(registered_client, database):
    client = registered_client
    enroll(client)
    with Session(database) as db:
        key = db.scalar(select(Passkey))
        key_id = key.id
        db.delete(db.get(UserPassword, key.user_id))
        db.commit()
    assert (
        client.post(
            "/account/settings",
            headers=headers(client, "/account/settings"),
            data={"action": "remove_passkey", "passkey_id": key_id},
        ).status_code
        == 409
    )


def test_passkey_only_account_can_set_initial_password(registered_client, database):
    client = registered_client
    auth = enroll(client)
    with Session(database) as db:
        key = db.scalar(select(Passkey))
        user_id = key.user_id
        db.delete(db.get(UserPassword, user_id))
        db.commit()
    assert assert_passkey(client, auth, "reauthenticate")[0].status_code == 200
    response = client.post(
        "/account/settings",
        headers=headers(client, "/account/settings"),
        data={"action": "password", "password": "initial password", "confirm": "initial password"},
    )
    assert response.status_code == 200
    with Session(database) as db:
        assert verify_password("initial password", db.get(UserPassword, user_id))


def test_rename_passkey_and_account_ownership(registered_client, database):
    client = registered_client
    enroll(client)
    with Session(database) as db:
        key_id = db.scalar(select(Passkey.id))
    assert (
        client.post(
            "/account/settings",
            headers=headers(client, "/account/settings"),
            data={"action": "rename_passkey", "passkey_id": key_id, "name": "Phone"},
        ).status_code
        == 200
    )
    with Session(database) as db:
        assert db.get(Passkey, key_id).name == "Phone"
    assert (
        client.post(
            "/account/settings",
            headers=headers(client, "/account/settings"),
            data={"action": "remove_passkey", "passkey_id": key_id + 1},
        ).status_code
        == 404
    )


def test_concurrent_removal_keeps_one_sign_in_method(registered_client, database):
    client = registered_client
    enroll(client)
    enroll(client)
    with TestClient(client.app, client=("127.0.0.2", 50001)) as other:
        other.post("/login", headers=headers(other), data={"username": "reader", "password": "correct horse"})
        h1 = headers(client, "/account/settings")
        h2 = headers(other, "/account/settings")
        with Session(database) as db:
            keys = list(db.scalars(select(Passkey)))
            ids = [key.id for key in keys]
            db.delete(db.get(UserPassword, keys[0].user_id))
            db.commit()
        with ThreadPoolExecutor() as pool:
            futures = [
                pool.submit(
                    c.post, "/account/settings", headers=h, data={"action": "remove_passkey", "passkey_id": key_id}
                )
                for c, h, key_id in [(client, h1, ids[0]), (other, h2, ids[1])]
            ]
            assert sorted(f.result(timeout=10).status_code for f in futures) == [200, 409]
    with Session(database) as db:
        assert len(list(db.scalars(select(Passkey)))) == 1


def test_concurrent_reset_consumes_grant_once(registered_client):
    client = registered_client
    auth = enroll(client)
    sign_out(client)
    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    h = headers(client, "/reset-password")
    with TestClient(client.app, client=("127.0.0.2", 50002)) as other:
        other.cookies.update(client.cookies)
        with ThreadPoolExecutor() as pool:
            futures = [
                pool.submit(
                    c.post,
                    "/reset-password",
                    headers=h,
                    data={"password": "new password value", "confirm": "new password value"},
                )
                for c in (client, other)
            ]
            assert sorted(f.result(timeout=10).status_code for f in futures) == [200, 400]


def test_explicit_language_choices_and_default_are_only_in_settings(registered_client):
    from bs4 import BeautifulSoup

    client = registered_client
    page = client.get("/account/settings", headers={"Accept-Language": "ru"})
    choices = BeautifulSoup(page.text, "html.parser").select("#account-language option")
    assert [choice["value"] for choice in choices] == ["default", "en", "ru"]
    assert choices[0].has_attr("selected")
    assert (
        client.post("/language", headers=headers(client, "/account/settings"), data={"language": "en"}).status_code
        == 200
    )
    assert '<html lang="en">' in client.get("/my_settings", headers={"Accept-Language": "ru"}).text
    assert (
        client.post("/language", headers=headers(client, "/account/settings"), data={"language": "xx"}).status_code
        == 422
    )


def test_authentication_query_logging_does_not_include_tokens():
    import logging

    from text_evolver.web.filters.access_log import AuthenticationQueryFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "test",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("client", "GET", "/auth/google/callback?code=secret&state=secret", "1.1", 303),
        None,
    )
    assert AuthenticationQueryFilter().filter(record)
    assert "secret" not in record.getMessage()


def test_removing_authorizing_passkey_invalidates_recovery(registered_client, database):
    client = registered_client
    auth = enroll(client)
    with TestClient(client.app) as other:
        other.get("/login")
        assert assert_passkey(other, auth, "recover")[0].status_code == 200
        with Session(database) as db:
            key_id = db.scalar(select(Passkey.id))
        response = client.post(
            "/account/settings",
            headers=headers(client, "/account/settings"),
            data={"action": "remove_passkey", "passkey_id": key_id},
        )
        assert response.status_code == 200
        assert other.get("/reset-password").status_code == 400
        assert assert_passkey(other, auth)[0].status_code == 400


def google_start(client, purpose="login", path="/login"):
    response = client.post(
        "/auth/google/start", data={"purpose": purpose}, headers=headers(client, path), follow_redirects=False
    )
    assert response.status_code == 303, response.text
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    return params["state"][0]


def enable_google(client, app_settings, monkeypatch, subject="google-subject"):
    app_settings.google_client_id = "test-client"
    from pydantic import SecretStr

    app_settings.google_client_secret = SecretStr("test-secret")

    async def exchange(http, *args):
        assert http is client.app.state.http_client
        return {"iss": google.ISSUER, "sub": subject, "email": "same@example.com"}

    monkeypatch.setattr(google, "exchange_and_verify", exchange)


def test_google_registration_linking_and_nonce_state_binding(client, app_settings, monkeypatch, database):
    enable_google(client, app_settings, monkeypatch)
    token = google_start(client)
    with TestClient(client.app) as other:
        other.get("/login")
        assert other.get("/auth/google/callback", params={"state": token, "code": "code"}).status_code == 400
    response = client.get("/auth/google/callback", params={"state": token, "code": "code"})
    assert "Complete Google registration" in response.text
    response = client.post(
        "/auth/google/complete", data={"username": "google-user"}, headers=headers(client, "/auth/google/complete")
    )
    assert response.status_code == 200
    with Session(database) as db:
        identity = db.scalar(select(GoogleIdentity))
        assert db.get(UserPassword, identity.user_id) is None
    response = client.post(
        "/account/settings", data={"action": "unlink_google"}, headers=headers(client, "/account/settings")
    )
    assert response.status_code == 409
    assert client.get("/auth/google/callback", params={"state": token, "code": "code"}).status_code == 400
    response = client.post(
        "/account/settings",
        data={"action": "password", "password": "google password", "confirm": "google password"},
        headers=headers(client, "/account/settings"),
    )
    assert response.status_code == 200
    assert (
        client.post(
            "/account/settings", data={"action": "unlink_google"}, headers=headers(client, "/account/settings")
        ).status_code
        == 200
    )


def test_google_links_explicitly_and_reauthenticates_same_identity(
    registered_client, app_settings, monkeypatch, database
):
    client = registered_client
    enable_google(client, app_settings, monkeypatch)
    token = google_start(client, "link", "/account/settings")
    assert client.get("/auth/google/callback", params={"state": token, "code": "code"}).status_code == 200
    expire_freshness(database, client)
    token = google_start(client, "reauthenticate", "/account/reauthenticate")
    before = browser_session(database, client).expires_at
    assert client.get("/auth/google/callback", params={"state": token, "code": "code"}).status_code == 200
    assert browser_session(database, client).expires_at == before


def test_google_id_token_validation(app_settings):
    from joserfc import jwt
    from joserfc.errors import JoseError
    from joserfc.jwk import RSAKey

    app_settings.google_client_id = "test-client"
    key = RSAKey.generate_key(parameters={"kid": "test"})
    public = {"keys": [key.as_dict()]}
    now = int(utils.utcnow().timestamp())
    claims = {
        "iss": google.ISSUER,
        "sub": "subject",
        "aud": "test-client",
        "iat": now,
        "exp": now + 300,
        "nonce": "nonce",
    }

    def encoded(**changes):
        return jwt.encode({"alg": "RS256", "kid": "test"}, {**claims, **changes}, key)

    assert google.validate_id_token(encoded(), public, app_settings, "nonce", "access")["sub"] == "subject"
    for bad in [{"aud": "other"}, {"iss": "https://evil.example"}, {"exp": now - 60}, {"nonce": "wrong"}]:
        with pytest.raises(JoseError):
            google.validate_id_token(encoded(**bad), public, app_settings, "nonce", "access")
    other_key = RSAKey.generate_key(parameters={"kid": "test"})
    forged = jwt.encode({"alg": "RS256", "kid": "test"}, claims, other_key)
    with pytest.raises(JoseError):
        google.validate_id_token(forged, public, app_settings, "nonce", "access")


def test_passkey_options_do_not_use_login_attempt_throttling(client, database):
    from text_evolver.db.models import LoginFailure

    h = headers(client)
    for _ in range(31):
        assert client.post("/auth/webauthn/login/options", json={}, headers=h).status_code == 200
    with Session(database) as db:
        assert db.scalar(select(LoginFailure)) is None


def test_recovery_clears_owners_failed_password_login_block(registered_client):
    client = registered_client
    auth = enroll(client)
    sign_out(client)
    h = headers(client)
    for _ in range(5):
        assert (
            client.post("/login", headers=h, data={"username": "reader", "password": "wrong password"}).status_code
            == 422
        )
    assert client.post("/login", headers=h, data={"username": "reader", "password": "correct horse"}).status_code == 429
    assert assert_passkey(client, auth, "recover")[0].status_code == 200
    assert (
        client.post(
            "/reset-password",
            headers=headers(client, "/reset-password"),
            data={"password": "replacement password", "confirm": "replacement password"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/login", headers=headers(client), data={"username": "reader", "password": "replacement password"}
        ).status_code
        == 200
    )


def test_passkey_limit_is_enforced_during_concurrent_verification(registered_client, database):
    client = registered_client
    enroll(client)
    enroll(client)
    with TestClient(client.app) as other:
        other.post(
            "/login", data={"username": "reader", "password": "correct horse"}, headers=headers(other),
        )
        clients = [client, other]
        registrations = []
        for browser in clients:
            request_headers = headers(browser, "/account/settings")
            options = browser.post("/auth/webauthn/register/options", json={}, headers=request_headers).json()
            authenticator = Authenticator()
            registrations.append({
                "headers": request_headers,
                "json": {"flow": options["flow"], "credential": authenticator.registration(options["publicKey"]),
                         "name": "Concurrent"},
            })
        barrier = threading.Barrier(2)

        def verify(index):
            barrier.wait(timeout=10)
            return clients[index].post("/auth/webauthn/register/verify", **registrations[index]).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(verify, range(2)))
        assert sorted(results) == [200, 409]
    with Session(database) as db:
        assert len(list(db.scalars(select(Passkey)))) == 3
    assert client.post("/auth/webauthn/register/options", json={}, headers=headers(client)).status_code == 409
    page = client.get("/account/settings")
    assert "A maximum of 3 passkeys is allowed." in page.text
    assert 'data-webauthn="register"' not in page.text

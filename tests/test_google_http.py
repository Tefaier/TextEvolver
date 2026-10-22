import asyncio
import time
from base64 import b64encode
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from text_evolver.config import AppSettings
from text_evolver.main import create_app
from text_evolver.web.routines import google


@pytest.fixture
def google_settings(tmp_path):
    return AppSettings(
        _env_file=None,
        WORK_ROOT=tmp_path / "work",
        TEMP_ROOT=tmp_path / "temp",
        SELENIUMBASE_DRIVER_ROOT=tmp_path / "drivers",
        GOOGLE_CLIENT_ID="test-client",
        GOOGLE_CLIENT_SECRET="test-secret",
    )


@pytest.mark.parametrize("exceptional_exit", [False, True])
def test_app_lifespan_closes_shared_http_client(google_settings, exceptional_exit):
    application = create_app(google_settings)

    def run_application():
        with TestClient(application):
            shared = application.state.http_client
            assert isinstance(shared, httpx.AsyncClient)
            assert not shared.is_closed
            assert shared.timeout == httpx.Timeout(10)
            if exceptional_exit:
                raise RuntimeError("Test application failure")

    if exceptional_exit:
        with pytest.raises(RuntimeError, match="Test application failure"):
            run_application()
    else:
        run_application()
    assert application.state.http_client.is_closed


def test_google_requests_share_client_without_sharing_flow_state(google_settings):
    key = RSAKey.generate_key(parameters={"kid": "test"})
    now = int(time.time())
    requests = []

    async def provider(request):
        requests.append(request)
        await asyncio.sleep(0)
        if str(request.url) == google.TOKEN:
            assert request.method == "POST"
            form = parse_qs(request.content.decode())
            code = form["code"][0]
            assert form["grant_type"] == ["authorization_code"]
            assert form["code_verifier"] == ["verifier-" + code]
            assert form["redirect_uri"] == [google_settings.app_base_url + "/auth/google/callback"]
            assert request.headers["authorization"] == "Basic " + b64encode(b"test-client:test-secret").decode()
            token = jwt.encode(
                {"alg": "RS256", "kid": "test"},
                {
                    "iss": google.ISSUER,
                    "sub": "subject-" + code,
                    "aud": "test-client",
                    "iat": now,
                    "exp": now + 300,
                    "nonce": "nonce-" + code,
                },
                key,
            )
            return httpx.Response(200, json={"id_token": token, "access_token": "access-" + code})
        assert str(request.url) == google.JWKS
        assert request.method == "GET"
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"keys": [key.as_dict()]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as shared:
            results = await asyncio.gather(*(
                google.exchange_and_verify(shared, google_settings, code, "verifier-" + code, "nonce-" + code)
                for code in ("first", "second")
            ))
            assert [result["sub"] for result in results] == ["subject-first", "subject-second"]
            assert not shared.is_closed
            assert len(requests) == 4

    asyncio.run(run())

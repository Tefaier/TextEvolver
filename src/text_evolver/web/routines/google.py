import httpx
from authlib.oauth2.client import OAuth2Client
from authlib.oidc.core import CodeIDToken
from fastapi import HTTPException
from joserfc import jwt
from joserfc.jwk import KeySet

from text_evolver.config import AppSettings

ISSUER = "https://accounts.google.com"
AUTHORIZE = ISSUER + "/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
JWKS = "https://www.googleapis.com/oauth2/v3/certs"


def client(settings: AppSettings) -> OAuth2Client:
    if not settings.google_client_id or not settings.google_client_secret.get_secret_value():
        raise HTTPException(503, "Google sign-in is not configured.")
    return OAuth2Client(
        session=None,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        scope="openid email profile",
        code_challenge_method="S256",
        redirect_uri=settings.app_base_url + "/auth/google/callback",
    )


def validate_id_token(encoded: str, jwks: dict, settings: AppSettings, nonce: str, access_token: str) -> dict:
    decoded = jwt.decode(encoded, KeySet.import_key_set(jwks), algorithms=["RS256"])
    claims = CodeIDToken(
        decoded.claims,
        decoded.header,
        options={
            "iss": {"essential": True, "values": [ISSUER, "accounts.google.com"]},
            "aud": {"essential": True, "value": settings.google_client_id},
            "sub": {"essential": True},
            "exp": {"essential": True},
            "iat": {"essential": True},
        },
        params={"nonce": nonce, "client_id": settings.google_client_id, "access_token": access_token},
    )
    claims.validate(leeway=30)
    return dict(claims)


async def exchange_and_verify(
    http: httpx.AsyncClient, settings: AppSettings, code: str, verifier: str, nonce: str,
) -> dict:
    oauth = client(settings)
    token_response = await http.post(
        TOKEN,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": oauth.redirect_uri,
        },
        auth=(oauth.client_id, oauth.client_secret),
    )
    token_response.raise_for_status()
    token = oauth.parse_response_token(token_response)
    response = await http.get(JWKS)
    response.raise_for_status()
    return validate_id_token(token["id_token"], response.json(), settings, nonce, token.get("access_token", ""))

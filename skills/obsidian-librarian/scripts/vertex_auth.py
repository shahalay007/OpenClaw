#!/usr/bin/env python3
"""Vertex AI OAuth token helper.

Builds an RS256 JWT from a Google service-account JSON, signs it via the
`openssl` CLI to avoid pulling in a Python crypto dependency, exchanges it
for an access token, and caches the token in-process until expiry.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Lock


OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

_token_cache: dict = {"token": None, "expires_at": 0.0, "creds_path": ""}
_cache_lock = Lock()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_credentials(path: str) -> dict:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise RuntimeError(f"Credentials JSON not found at {resolved}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    cred_type = payload.get("type")
    if cred_type == "service_account":
        for required in ("client_email", "private_key"):
            if required not in payload:
                raise RuntimeError(f"Service account JSON missing field: {required}")
    elif cred_type == "authorized_user":
        for required in ("client_id", "client_secret", "refresh_token"):
            if required not in payload:
                raise RuntimeError(f"Authorized-user JSON missing field: {required}")
    else:
        raise RuntimeError(
            f"Unsupported credentials type: {cred_type!r}. "
            "Expected 'service_account' or 'authorized_user'."
        )
    return payload


def _sign_rs256(message: str, private_key_pem: str) -> bytes:
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as key_file:
        key_file.write(private_key_pem)
        key_path = key_file.name
    try:
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=message.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"openssl signing failed: {result.stderr.decode('utf-8', errors='replace')}"
            )
        return result.stdout
    finally:
        try:
            os.unlink(key_path)
        except OSError:
            pass


def _build_jwt(sa: dict) -> str:
    now = int(time.time())
    header = _b64url(
        json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    claim = _b64url(
        json.dumps(
            {
                "iss": sa["client_email"],
                "scope": SCOPE,
                "aud": sa.get("token_uri") or OAUTH_TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signing_input = f"{header}.{claim}"
    signature = _b64url(_sign_rs256(signing_input, sa["private_key"]))
    return f"{signing_input}.{signature}"


def _exchange_jwt_for_token(sa: dict) -> tuple[str, float]:
    assertion = _build_jwt(sa)
    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode("utf-8")
    token_url = sa.get("token_uri") or OAUTH_TOKEN_URL
    return _post_token_request(token_url, body)


def _exchange_refresh_token(user: dict) -> tuple[str, float]:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": user["client_id"],
            "client_secret": user["client_secret"],
            "refresh_token": user["refresh_token"],
        }
    ).encode("utf-8")
    return _post_token_request(OAUTH_TOKEN_URL, body)


def _post_token_request(url: str, body: bytes) -> tuple[str, float]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if "access_token" not in payload:
        raise RuntimeError(f"Token exchange response missing access_token: {payload}")
    expires_in = int(payload.get("expires_in", 3600))
    # Refresh one minute early to avoid edge-of-expiry failures.
    return payload["access_token"], time.time() + max(60, expires_in - 60)


def get_vertex_access_token(credentials_path: str) -> str:
    if not credentials_path:
        raise RuntimeError(
            "Vertex access token requested without GOOGLE_APPLICATION_CREDENTIALS"
        )
    with _cache_lock:
        if (
            _token_cache["token"]
            and _token_cache["creds_path"] == credentials_path
            and time.time() < _token_cache["expires_at"]
        ):
            return _token_cache["token"]
        creds = _load_credentials(credentials_path)
        if creds.get("type") == "authorized_user":
            token, expires_at = _exchange_refresh_token(creds)
        else:
            token, expires_at = _exchange_jwt_for_token(creds)
        _token_cache["token"] = token
        _token_cache["expires_at"] = expires_at
        _token_cache["creds_path"] = credentials_path
        return token

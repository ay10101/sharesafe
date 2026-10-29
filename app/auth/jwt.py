# app/auth/jwt.py
"""
Lightweight, secure HMAC-SHA256 token service for ShareSafe authentication.
Zero-dependency implementation using standard library hmac and hashlib.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

SECRET_KEY = os.getenv("AUTH_SECRET", "sharesafe-hackathon-hmac-secret-key-48h")
TOKEN_EXPIRY_SECONDS = 86400 * 7  # 7 days for demo ease


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding and padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_access_token(user_id: str, role: str, name: str, expires_in: int = TOKEN_EXPIRY_SECONDS) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": role,
        "name": name,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in,
    }
    
    header_bytes = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_bytes = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_bytes}.{payload_bytes}".encode("utf-8")
    
    sig = hmac.new(SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_bytes = _b64url_encode(sig)
    
    return f"{header_bytes}.{payload_bytes}.{sig_bytes}"


def verify_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), signing_input, hashlib.sha256).digest()
        
        if not hmac.compare_digest(_b64url_encode(expected_sig), sig_b64):
            return None
            
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        if payload.get("exp", 0) < time.time():
            return None  # Expired
            
        return payload
    except Exception:
        return None


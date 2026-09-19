"""
安全模块：密码哈希、会话令牌、API Key 加密

说明（Demo 取舍，已明确标注）：
  * 密码：PBKDF2-HMAC-SHA256，20 万次迭代 + 随机盐，标准库实现，强度足够；
  * 令牌：HMAC-SHA256 签名的自包含 token（JWT 的极简等价物），
         上线时可直接换成 PyJWT，接口不变；
  * 第三方 API Key：用 SECRET_KEY 派生 Fernet 密钥做对称加密后入库，
         数据库被拖走也拿不到明文。生产环境务必把 SECRET_KEY 放进
         环境变量 / 密钥管理服务，不要用默认值。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.fernet import Fernet

# ------------------------------------------------------------------ 基础参数
SECRET_KEY = os.environ.get("LEARNHUB_SECRET", "dev-only-change-me-in-production")
TOKEN_TTL_SECONDS = int(os.environ.get("LEARNHUB_TOKEN_TTL", str(7 * 24 * 3600)))
PBKDF2_ROUNDS = 200_000


# ------------------------------------------------------------------ 密码
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ROUNDS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expect = base64.b64decode(dk_b64)
        got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(got, expect)
    except Exception:
        return False


# ------------------------------------------------------------------ 令牌
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(txt: str) -> bytes:
    return base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))


def _sign(payload_b64: str) -> str:
    mac = hmac.new(SECRET_KEY.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256)
    return _b64e(mac.digest())


def create_token(user_id: int, role: str, name: str) -> str:
    payload = {"sub": user_id, "role": role, "name": name,
               "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def decode_token(token: str) -> dict | None:
    try:
        payload_b64, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(payload_b64)):
            return None
        payload = json.loads(_b64d(payload_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


# ------------------------------------------------------------------ API Key 加密
def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")


def mask_key(key: str) -> str:
    """只回显后 4 位，前端永远拿不到完整 key。"""
    if not key:
        return ""
    return "••••••••" + key[-4:] if len(key) > 4 else "••••"

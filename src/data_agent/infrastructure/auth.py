"""认证与权限（ADR-0003：自建账号体系，bcrypt + JWT，用户级隔离）。"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import bcrypt
import jwt

JWT_ALGORITHM = "HS256"
# 独立项目无现成密钥管理：环境变量注入；未设置时用随机密钥（仅开发，重启即失效）
_JWT_SECRET = os.environ.get("DATA_AGENT_JWT_SECRET", "")
JWT_TTL_SECONDS = int(os.environ.get("DATA_AGENT_JWT_TTL", str(7 * 24 * 3600)))


@dataclass
class TokenPayload:
    user_id: str
    username: str


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def issue_token(user_id: str, username: str) -> str:
    secret = _JWT_SECRET or os.environ.get("DATA_AGENT_JWT_SECRET", "")
    if not secret:
        # 开发兜底：进程内固定随机密钥（重启后旧 token 失效）
        import secrets as _secrets

        secret = getattr(issue_token, "_dev_secret", None) or _secrets.token_hex(32)
        setattr(issue_token, "_dev_secret", secret)
    payload = {"sub": user_id, "name": username, "exp": int(time.time()) + JWT_TTL_SECONDS}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> TokenPayload | None:
    secret = os.environ.get("DATA_AGENT_JWT_SECRET", "") or getattr(issue_token, "_dev_secret", "")
    if not secret:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
        return TokenPayload(user_id=payload["sub"], username=payload.get("name", ""))
    except jwt.PyJWTError:
        return None

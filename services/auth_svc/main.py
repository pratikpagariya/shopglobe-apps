"""auth-svc: issues and verifies JWTs. Signing key comes from Secrets Manager
via External Secrets in-cluster -- never from git."""

import time

import jwt
from fastapi import Header, HTTPException
from pydantic import BaseModel

from libs.common import Settings
from libs.common.app import make_app, run

S = Settings(service_name="auth-svc")


async def _check_secret() -> None:
    if S.env != "dev" and S.jwt_secret == "dev-only-change-me":
        raise RuntimeError("jwt_secret was not injected")


app = make_app(S, {"secret": _check_secret})


class TokenRequest(BaseModel):
    user_id: str


@app.post("/token")
async def issue(req: TokenRequest) -> dict:
    now = int(time.time())
    claims = {
        "sub": req.user_id,
        "iat": now,
        "exp": now + S.jwt_ttl_seconds,
        "market": S.market,
        "scope": "customer",
    }
    # `kid` is how you rotate with zero downtime: publish the new key alongside
    # the old, accept both until every old token has expired, then drop the old.
    token = jwt.encode(claims, S.jwt_secret, algorithm="HS256", headers={"kid": S.jwt_kid})
    return {"access_token": token, "expires_in": S.jwt_ttl_seconds, "kid": S.jwt_kid}


@app.get("/verify")
async def verify(authorization: str = Header("")) -> dict:
    # 401 = "I don't know who you are". Contrast with /admin below.
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(authorization[7:], S.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired") from None
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    return {"valid": True, "claims": claims}


@app.get("/admin")
async def admin(authorization: str = Header("")) -> dict:
    """Exists purely to demonstrate 401 vs 403. 401 = unauthenticated (who are
    you?). 403 = authenticated but not permitted (I know you, and no)."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(authorization[7:], S.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid token") from None
    if claims.get("scope") != "admin":
        raise HTTPException(403, "scope 'admin' required")
    return {"ok": True}


if __name__ == "__main__":
    run(app, S)

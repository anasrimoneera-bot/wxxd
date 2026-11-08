from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import User
from .security import decode_token


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    try:
        payload = decode_token(authorization[7:])
    except Exception:
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(401, "账号不可用")
    return user


def owner_user(user: User = Depends(current_user)) -> User:
    if user.role != "owner":
        raise HTTPException(403, "仅主账号可操作")
    return user

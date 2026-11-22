"""Password authentication, sessions and role permissions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings
from app.database import Database, utcnow
from app.models import AuthSession, User

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "platform.view",
        "platform.submit",
        "platform.operate",
        "platform.assets",
        "admin.console",
        "admin.users",
        "admin.settings",
    },
    "operator": {
        "platform.view",
        "platform.submit",
        "platform.operate",
        "platform.assets",
    },
    "viewer": {"platform.view"},
}
ROLES = tuple(ROLE_PERMISSIONS)
PASSWORD_ITERATIONS = 310_000


class AuthError(RuntimeError):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


def permissions_for(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role, set()))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db

    def ensure_bootstrap_admin(self) -> None:
        with self.db.session() as session:
            if session.scalar(select(func.count(User.id))) or 0:
                return
            username = self.settings.bootstrap_admin_username.strip() or "admin"
            session.add(
                User(
                    id="u-" + uuid.uuid4().hex[:12],
                    username=username,
                    password_hash=hash_password(self.settings.bootstrap_admin_password),
                    role="admin",
                    active=True,
                )
            )
            session.flush()

    def login(self, username: str, password: str) -> tuple[str, User, object]:
        with self.db.session() as session:
            user = session.scalar(select(User).where(User.username == username.strip()))
            if user is None or not user.active or not verify_password(password, user.password_hash):
                raise AuthError("用户名或密码错误", 401)
            now = utcnow()
            token = secrets.token_urlsafe(32)
            expires_at = now + timedelta(hours=self.settings.auth_session_hours)
            session.execute(
                AuthSession.__table__.delete().where(AuthSession.expires_at <= now)
            )
            session.add(
                AuthSession(
                    token_hash=_token_hash(token),
                    user_id=user.id,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            user.last_login_at = now
            session.flush()
            session.expunge(user)
            return token, user, expires_at

    def authenticate(self, token: str | None) -> User | None:
        if not token:
            return None
        with self.db.session() as session:
            record = session.get(AuthSession, _token_hash(token))
            if record is None or record.expires_at <= utcnow():
                return None
            user = session.get(User, record.user_id)
            if user is None or not user.active:
                return None
            session.expunge(user)
            return user

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self.db.session() as session:
            record = session.get(AuthSession, _token_hash(token))
            if record is not None:
                session.delete(record)
                session.flush()

    def list_users(self) -> list[User]:
        with self.db.session() as session:
            users = session.scalars(select(User).order_by(User.created_at.asc())).all()
            for user in users:
                session.expunge(user)
            return users

    def _active_admin_count(self, session) -> int:
        return int(
            session.scalar(
                select(func.count(User.id)).where(User.role == "admin", User.active.is_(True))
            )
            or 0
        )

    def create_user(self, username: str, password: str, role: str) -> User:
        username = username.strip()
        if len(username) < 3 or len(username) > 64:
            raise AuthError("用户名长度需要 3–64 个字符", 422)
        if len(password) < 6:
            raise AuthError("密码至少需要 6 个字符", 422)
        if role not in ROLES:
            raise AuthError("无效的角色", 422)
        with self.db.session() as session:
            if session.scalar(select(User).where(User.username == username)):
                raise AuthError("用户名已存在", 409)
            user = User(
                id="u-" + uuid.uuid4().hex[:12],
                username=username,
                password_hash=hash_password(password),
                role=role,
                active=True,
            )
            session.add(user)
            session.flush()
            session.expunge(user)
            return user

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        active: bool | None = None,
        password: str | None = None,
    ) -> User:
        with self.db.session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise AuthError("用户不存在", 404)
            if role is not None:
                if role not in ROLES:
                    raise AuthError("无效的角色", 422)
                if user.role == "admin" and role != "admin":
                    if self._active_admin_count(session) <= 1:
                        raise AuthError("至少保留一个启用的管理员", 409)
                user.role = role
            if active is not None:
                if user.role == "admin" and not active and self._active_admin_count(session) <= 1:
                    raise AuthError("至少保留一个启用的管理员", 409)
                user.active = active
            if password is not None:
                if len(password) < 6:
                    raise AuthError("密码至少需要 6 个字符", 422)
                user.password_hash = hash_password(password)
            session.flush()
            session.expunge(user)
            return user

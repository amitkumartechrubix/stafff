from passlib.context import CryptContext
from sqlalchemy.orm import Session
from datetime import datetime
from models.user import User, UserRole, LicenseType

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def authenticate_user(db: Session, username_or_email: str, password: str):
    """Authenticate by username OR email. Returns User or None."""
    user = db.query(User).filter(
        (User.username == username_or_email) | (User.email == username_or_email)
    ).first()
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


def create_user(db: Session, **kwargs) -> User:
    password = kwargs.pop("password")
    kwargs["hashed_password"] = hash_password(password)
    user = User(**kwargs)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_last_login(db: Session, user: User) -> None:
    user.last_login = datetime.utcnow()
    db.commit()

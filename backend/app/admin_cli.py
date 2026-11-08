"""Grant or revoke platform admin:  python -m app.admin_cli grant <username>"""
import sys

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import User


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("grant", "revoke"):
        print("usage: python -m app.admin_cli grant|revoke <username>")
        sys.exit(2)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.username == sys.argv[2]))
        if not u:
            print("user not found")
            sys.exit(1)
        u.is_admin = sys.argv[1] == "grant"
        db.commit()
        print(f"{u.username}: is_admin={u.is_admin}")


if __name__ == "__main__":
    main()

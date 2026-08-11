"""演示数据与模板初始化测试。"""

from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.enums import UserRole
from app.models import Task, TaskTemplate, User
from app.security import hash_password, verify_password
from app.seed import seed_demo_data, seed_templates


def test_seed_demo_data_in_fresh_database(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'seed.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        admin = User(
            username="seed-admin",
            display_name="初始化管理员",
            password_hash=hash_password("PartyOps@2026"),
            role=UserRole.ADMIN,
        )
        db.add(admin)
        db.commit()
        seed_demo_data(db, admin)
        assert db.scalar(select(func.count()).select_from(TaskTemplate)) >= 5
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        seeded_staff = db.scalar(select(User).where(User.username == "xietong"))
        assert seeded_staff is not None
        assert not verify_password("PartyOps@2026", seeded_staff.password_hash)
        seed_templates(db, admin)
        seed_demo_data(db, admin)
        assert db.scalar(select(func.count()).select_from(Task)) == 2
    engine.dispose()

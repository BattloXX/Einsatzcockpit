"""Legt nach ``app.seed_data`` einen rein synthetischen CI-Boardfall an."""
from app.core.tenant import set_tenant_context
from app.db import SessionLocal
from app.models.incident import Task
from app.models.user import User
from app.services.incident_service import add_task, create_incident, prepend_card


def main() -> None:
    db = SessionLocal()
    set_tenant_context(db, None)
    try:
        user = db.query(User).order_by(User.id).first()
        if user is None:
            raise RuntimeError("Bootstrap-Admin fehlt")
        set_tenant_context(db, user.org_id)
        incident, _ = create_incident(
            db,
            "T1",
            is_exercise=True,
            address_city="E2E",
            reason="E2E",
            incident_leader_user_id=user.id,
            primary_org_id=user.org_id,
        )
        task: Task = add_task(db, incident, "E2E", user_id=user.id)
        prepend_card(db, task.column_id, "task", task.id)
        db.commit()
        print(incident.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()

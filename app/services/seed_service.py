"""Seed-Profil-Service: kopiert SeedTemplate-Einträge in eine neue Org."""
import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.master import (
    AIPromptVersion,
    AlarmType,
    DefaultMessage,
    DefaultMessageAlarm,
    LageHint,
    LageHintAlarm,
    MessageSuggestion,
    MessageSuggestionAlarm,
    SeedTemplate,
    TaskSuggestion,
    TaskSuggestionAlarm,
)


def list_profiles(db: Session) -> list[dict]:
    """Gibt alle verfügbaren Seed-Profile zurück (ohne Duplikate)."""
    rows = (
        db.query(SeedTemplate.profile, SeedTemplate.profile_label)
        .distinct()
        .order_by(SeedTemplate.profile_label)
        .all()
    )
    return [{"profile": r.profile, "label": r.profile_label} for r in rows]


def apply_seed_profile(db: Session, org_id: int, profile: str) -> None:
    """Kopiert alle Templates eines Profils in die Org (idempotent per Alarmtyp-Code)."""
    templates = (
        db.query(SeedTemplate)
        .filter(SeedTemplate.profile == profile)
        .order_by(SeedTemplate.type, SeedTemplate.display_order)
        .all()
    )

    # 1. Alarm types
    alarm_type_map: dict[str, int] = {}
    for t in templates:
        if t.type != "alarm_type":
            continue
        d = json.loads(t.data)
        code = d["code"]
        existing = (
            db.query(AlarmType)
            .filter(AlarmType.org_id == org_id, AlarmType.code == code)
            .first()
        )
        if existing:
            alarm_type_map[code] = existing.id
            continue
        at = AlarmType(
            org_id=org_id,
            code=code,
            category=d.get("category", "T"),
            label=d.get("label", code),
            default_first_train_only=d.get("default_first_train_only", False),
            notify_neighbors=d.get("notify_neighbors", False),
            triggers_major_incident=d.get("triggers_major_incident", False),
        )
        db.add(at)
        db.flush()
        alarm_type_map[code] = at.id

    # 2. Task suggestions
    for t in templates:
        if t.type != "task_suggestion":
            continue
        d = json.loads(t.data)
        s = TaskSuggestion(org_id=org_id, text=d["text"])
        db.add(s)
        db.flush()
        for i, code in enumerate(d.get("alarm_codes", [])):
            at_id = alarm_type_map.get(code)
            if at_id:
                db.add(TaskSuggestionAlarm(
                    task_suggestion_id=s.id, alarm_type_id=at_id, display_order=i,
                ))

    # 3. Message suggestions
    for t in templates:
        if t.type != "message_suggestion":
            continue
        d = json.loads(t.data)
        s = MessageSuggestion(org_id=org_id, text=d["text"])  # type: ignore[assignment]
        db.add(s)
        db.flush()
        for i, code in enumerate(d.get("alarm_codes", [])):
            at_id = alarm_type_map.get(code)
            if at_id:
                db.add(MessageSuggestionAlarm(
                    message_suggestion_id=s.id, alarm_type_id=at_id, display_order=i,
                ))

    # 4. Lage hints
    for i_hint, t in enumerate(t for t in templates if t.type == "lage_hint"):
        d = json.loads(t.data)
        h = LageHint(org_id=org_id, text=d["text"], display_order=i_hint)
        db.add(h)
        db.flush()
        for i, code in enumerate(d.get("alarm_codes", [])):
            at_id = alarm_type_map.get(code)
            if at_id:
                db.add(LageHintAlarm(
                    lage_hint_id=h.id, alarm_type_id=at_id, display_order=i,
                ))

    # 5. Default messages
    for t in templates:
        if t.type != "default_message":
            continue
        d = json.loads(t.data)
        m = DefaultMessage(org_id=org_id, text=d["text"])
        db.add(m)
        db.flush()
        for i, code in enumerate(d.get("alarm_codes", [])):
            at_id = alarm_type_map.get(code)
            if at_id:
                db.add(DefaultMessageAlarm(
                    default_message_id=m.id,
                    alarm_type_id=at_id,
                    display_order=i,
                    due_after_sec=d.get("due_after_sec", 300),
                ))

    # 6. Objektverwaltungs-Kataloge (Kategorien/Gefahren/Merkmale, idempotent)
    from app.services.objekt_service import seed_objekt_kataloge
    seed_objekt_kataloge(db, org_id)

    db.flush()


def copy_default_prompts(db: Session, org_id: int) -> None:
    """Creates one AIPromptVersion per PROMPT_META key for org_id (idempotent)."""
    from app.services.ai_service import PROMPT_META

    for key, meta in PROMPT_META.items():
        exists = (
            db.query(AIPromptVersion)
            .filter(AIPromptVersion.org_id == org_id, AIPromptVersion.prompt_key == key)
            .first()
        )
        if exists:
            continue
        db.add(AIPromptVersion(
            org_id=org_id,
            prompt_key=key,
            version=1,
            variable_part=meta["variable_default"],
            note="Standard (automatisch erstellt)",
            created_at=datetime.now(UTC),
        ))
    db.flush()

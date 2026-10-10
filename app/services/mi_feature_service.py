"""Gemeinsame effektive Feature-Schalter der Grossschadenslage."""

from sqlalchemy.orm import Session

from app.models.master import OrgSettings, SystemSettings

_MI_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        "mi_feature_stab",
        "mi_feature_funkjournal",
        "mi_feature_meldungen",
        "mi_feature_sektoren",
        "mi_feature_karte",
        "mi_feature_zeitreise",
        "mi_feature_ressourcen",
        "mi_feature_uebergreifend",
        "mi_feature_geraeteverleih",
    }
)


def get_mi_features(db: Session, org_id: int | None = None) -> dict[str, bool]:
    """Liest globale und organisationsbezogene MI-Feature-Schalter."""
    rows = db.query(SystemSettings).filter(SystemSettings.key.in_(_MI_FEATURE_KEYS)).all()
    global_cfg = {row.key: row.value != "false" for row in rows}
    org = db.query(OrgSettings).filter_by(org_id=org_id).first() if org_id is not None else None

    def enabled(key: str) -> bool:
        return global_cfg.get(f"mi_feature_{key}", True) and bool(
            getattr(org, f"mi_feature_{key}", True) if org is not None else True
        )

    return {
        key: enabled(key)
        for key in (
            "stab",
            "funkjournal",
            "meldungen",
            "sektoren",
            "karte",
            "zeitreise",
            "ressourcen",
            "uebergreifend",
            "geraeteverleih",
        )
    }

"""
Seed-Daten: Stammdaten 1:1 aus der HTML-Version übernommen.
Aufruf: python -m app.seed_data
"""
from app.db import SessionLocal
from app.models.master import (
    AlarmType,
    DefaultMessage,
    FireDept,
    LageHint,
    Qualification,
    TaskSuggestion,
    VehicleMaster,
)
from app.models.user import Role

ROLES = [
    {"code": "system_admin",         "label": "Systemadministrator (organisationsübergreifend)"},
    {"code": "admin",                "label": "Administrator (Organisations-Admin)"},
    {"code": "org_admin",            "label": "Organisations-Administrator"},
    {"code": "incident_leader",      "label": "Einsatzleiter"},
    {"code": "breathing_supervisor", "label": "AS-Überwacher"},
    {"code": "recorder",             "label": "Schriftführer"},
    {"code": "readonly",             "label": "Nur Lesen"},
]

ALARM_TYPES = [
    {"code": "F1",  "category": "F", "label": "Brand Klein",         "default_first_train_only": True,  "notify_neighbors": False},
    {"code": "F2",  "category": "F", "label": "Brand Mittel",        "default_first_train_only": True,  "notify_neighbors": False},
    {"code": "F3",  "category": "F", "label": "Brand Groß",          "default_first_train_only": False, "notify_neighbors": False},
    {"code": "F4",  "category": "F", "label": "Brand Katastrophe",   "default_first_train_only": False, "notify_neighbors": True},
    {"code": "F14", "category": "F", "label": "Fahrzeugbrand",       "default_first_train_only": True,  "notify_neighbors": False},
    {"code": "T1",  "category": "T", "label": "Technisch Klein",     "default_first_train_only": True,  "notify_neighbors": False},
    {"code": "T2",  "category": "T", "label": "Technisch Mittel",    "default_first_train_only": True,  "notify_neighbors": False},
    {"code": "T3",  "category": "T", "label": "Technisch Groß",      "default_first_train_only": False, "notify_neighbors": True},
    {"code": "T4",  "category": "T", "label": "Technisch Komplex",   "default_first_train_only": False, "notify_neighbors": False},
    {"code": "T6",  "category": "T", "label": "Wasserrettung",       "default_first_train_only": False, "notify_neighbors": True},
    {"code": "T7",  "category": "T", "label": "Technisch Speziell",  "default_first_train_only": False, "notify_neighbors": True},
]

FIRE_DEPTS = [
    {"slug": "wolfurt",   "name": "FF Wolfurt",       "color": "#b71921", "is_home_org": True,  "bos": "Feuerwehr"},
    {"slug": "lauterach", "name": "FF Lauterach",     "color": "#1877f2", "is_home_org": False, "bos": "Feuerwehr"},
    {"slug": "schwarzach","name": "FF Schwarzach",    "color": "#8e44ad", "is_home_org": False, "bos": "Feuerwehr"},
    {"slug": "bildstein", "name": "FF Bildstein",     "color": "#2e9d55", "is_home_org": False, "bos": "Feuerwehr"},
    {"slug": "bregenz",   "name": "FF Bregenz-Stadt", "color": "#e67e22", "is_home_org": False, "bos": "Feuerwehr"},
    {"slug": "kennelbach","name": "OF Kennelbach",    "color": "#00a6a6", "is_home_org": False, "bos": "Feuerwehr"},
]

WOLFURT_VEHICLES = [
    {"code": "KDOF",  "name": "KDOF",  "type": "Kommandofunk / Kommandofahrzeug", "is_first_train": True,  "display_order": 0},
    {"code": "RLF",   "name": "RLF",   "type": "Rüstlöschfahrzeug",               "is_first_train": True,  "display_order": 1},
    {"code": "TMB",   "name": "TMB",   "type": "Teleskopmastbühne",               "is_first_train": True,  "display_order": 2},
    {"code": "LFB-C", "name": "LFB-C", "type": "Löschfahrzeug Berge C",           "is_first_train": True,  "display_order": 3},
    {"code": "Tank",  "name": "Tank",  "type": "Tanklöschfahrzeug",               "is_first_train": True,  "display_order": 4},
    {"code": "LF",    "name": "LF",    "type": "Löschfahrzeug",                   "is_first_train": False, "display_order": 5},
    {"code": "VF",    "name": "VF",    "type": "Versorgungsfahrzeug",             "is_first_train": False, "display_order": 6},
    {"code": "MTF",   "name": "MTF",   "type": "Mannschaftstransportfahrzeug",    "is_first_train": False, "display_order": 7},
    {"code": "MTF-2", "name": "MTF-2", "type": "Mannschaftstransportfahrzeug 2",  "is_first_train": False, "display_order": 8},
]

NEIGHBOR_VEHICLES = {
    "lauterach": [
        {"code": "KDOF-LA",  "name": "KDOF",  "type": "Kommandofahrzeug",     "display_order": 0},
        {"code": "RLF-LA",   "name": "RLF",   "type": "Rüstlöschfahrzeug",    "display_order": 1},
        {"code": "TLF-LA",   "name": "TLF",   "type": "Tanklöschfahrzeug",    "display_order": 2},
        {"code": "LF-LA",    "name": "LF",    "type": "Löschfahrzeug",        "display_order": 3},
        {"code": "MTF-LA",   "name": "MTF",   "type": "Mannschaftstransport", "display_order": 4},
        {"code": "GW-LA",    "name": "GW",    "type": "Gerätewagen",          "display_order": 5},
    ],
    "schwarzach": [
        {"code": "KDOF-SX",  "name": "KDOF",  "type": "Kommandofahrzeug",     "display_order": 0},
        {"code": "RLF-SX",   "name": "RLF",   "type": "Rüstlöschfahrzeug",    "display_order": 1},
        {"code": "TLF-SX",   "name": "TLF",   "type": "Tanklöschfahrzeug",    "display_order": 2},
        {"code": "DLK-SX",   "name": "DLK",   "type": "Drehleiter",           "display_order": 3},
        {"code": "LF1-SX",   "name": "LF 1",  "type": "Löschfahrzeug 1",      "display_order": 4},
        {"code": "LF2-SX",   "name": "LF 2",  "type": "Löschfahrzeug 2",      "display_order": 5},
        {"code": "MTF1-SX",  "name": "MTF 1", "type": "Mannschaftstransport", "display_order": 6},
        {"code": "MTF2-SX",  "name": "MTF 2", "type": "Mannschaftstransport", "display_order": 7},
        {"code": "GW-SX",    "name": "GW",    "type": "Gerätewagen",          "display_order": 8},
    ],
    "bildstein": [
        {"code": "LF-BI",    "name": "LF",    "type": "Löschfahrzeug",        "display_order": 0},
        {"code": "MTF-BI",   "name": "MTF",   "type": "Mannschaftstransport", "display_order": 1},
        {"code": "GW-BI",    "name": "GW",    "type": "Gerätewagen",          "display_order": 2},
    ],
    "bregenz": [
        {"code": "KDOF-BZ",  "name": "KDOF",  "type": "Kommandofahrzeug",     "display_order": 0},
        {"code": "RLF-BZ",   "name": "RLF",   "type": "Rüstlöschfahrzeug",    "display_order": 1},
        {"code": "DLK-BZ",   "name": "DLK",   "type": "Drehleiter",           "display_order": 2},
        {"code": "TLF-BZ",   "name": "TLF",   "type": "Tanklöschfahrzeug",    "display_order": 3},
        {"code": "HLF-BZ",   "name": "HLF",   "type": "Hilfeleistungslöschfahrzeug", "display_order": 4},
        {"code": "GW1-BZ",   "name": "GW 1",  "type": "Gerätewagen 1",        "display_order": 5},
        {"code": "GW2-BZ",   "name": "GW 2",  "type": "Gerätewagen 2",        "display_order": 6},
        {"code": "MTF-BZ",   "name": "MTF",   "type": "Mannschaftstransport", "display_order": 7},
    ],
    "kennelbach": [
        {"code": "LF-KE",    "name": "LF",    "type": "Löschfahrzeug",        "display_order": 0},
        {"code": "TLF-KE",   "name": "TLF",   "type": "Tanklöschfahrzeug",    "display_order": 1},
        {"code": "MTF-KE",   "name": "MTF",   "type": "Mannschaftstransport", "display_order": 2},
        {"code": "GW-KE",    "name": "GW",    "type": "Gerätewagen",          "display_order": 3},
    ],
}

TASK_SUGGESTIONS = {
    "T1": ["Lage erkunden", "Wasserschaden eingrenzen", "Gefährdete Personen evakuieren",
           "Strom abstellen (lassen)", "Dokumentation sicherstellen"],
    "T2": ["Lage erkunden", "Technische Rettung vorbereiten", "Absperrung einrichten",
           "Verletzte versorgen", "Leitstelle Rückmeldung", "Wasserversorgung prüfen"],
    "T3": ["Lage erkunden", "Rettungstrupp einsetzen", "Spezialkräfte anfordern",
           "Absperrbereich erweitern", "Bereitstellungsraum einrichten",
           "Lagemeldung absetzen", "Nachschub organisieren"],
    "T4": ["Lage erkunden", "Spezialkräfte anfordern", "Gefahrenbereich sichern",
           "Bevölkerungsschutz informieren", "Fachberater anfordern"],
    "T6": ["Wasserrettung aktivieren", "Sicherungsposten einrichten", "Rettungsgeräte bereitstellen",
           "Taucher anfordern", "Rettungsboot einsetzen"],
    "T7": ["Lage erkunden", "Spezialkräfte anfordern", "Absperrung einrichten",
           "Fachberater anfordern", "Lagemeldung absetzen"],
    "F1": ["Löschangriff vorbereiten", "Wasserversorgung herstellen",
           "Atemschutz bereitstellen", "Personensuche", "Riegelstellung einrichten"],
    "F2": ["Löschangriff vorbereiten", "Wasserversorgung herstellen",
           "Atemschutztrupp einsetzen", "Atemschutzüberwachung einrichten",
           "Riegelstellung einrichten", "Leitstelle Rückmeldung"],
    "F3": ["Menschenrettung priorisieren", "Atemschutztrupp einsetzen",
           "Löschangriff / Innenangriff vorbereiten", "Atemschutzüberwachung einrichten",
           "Spezialkräfte / Atemschutzsammelplatz prüfen",
           "Wasserversorgung sicherstellen", "Lagemeldung absetzen"],
    "F4": ["Menschenrettung priorisieren", "Atemschutztrupp einsetzen",
           "Löschangriff / Innenangriff vorbereiten", "Atemschutzüberwachung einrichten",
           "Spezialkräfte / Atemschutzsammelplatz prüfen",
           "Wasserversorgung sicherstellen", "Lagemeldung absetzen",
           "Nachalarmierung prüfen"],
    "F14": ["Fahrzeugbrand sichern", "Verkehr absperren", "Löschmittel bereitstellen",
            "Personenrettung", "Gefahrgut prüfen"],
}

DEFAULT_MESSAGES = {
    "T1":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "T2":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "T3":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300},
            {"text": "Spezialkräfte prüfen/anfordern", "due_after_sec": 600}],
    "T4":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "T6":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "T7":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "F1":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "F2":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
    "F3":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300},
            {"text": "Spezialkräfte / Atemschutzsammelplatz prüfen", "due_after_sec": 600}],
    "F4":  [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300},
            {"text": "Spezialkräfte / Atemschutzsammelplatz prüfen", "due_after_sec": 600}],
    "F14": [{"text": "Lagemeldung an RFL absetzen", "due_after_sec": 300}],
}

LAGE_HINTS = [
    "Lage erkunden – eigene Kräfte schützen",
    "Gefahrenmatrix im Blick behalten",
    "Wasserversorgung frühzeitig sicherstellen",
    "Eigenschutz geht vor – PSA prüfen",
    "Abschnittsführer einweisen",
    "Regelmäßige Lagemeldungen absetzen",
    "Rückzugswege freihalten",
    "Atemschutzsammelplatz bestimmen",
]

QUALIFICATIONS = [
    {"code": "AGT",  "label": "Atemschutzgeräteträger"},
    {"code": "ATF",  "label": "Atemschutz-Truppführer"},
    {"code": "MA",   "label": "Maschinist"},
    {"code": "GK",   "label": "Gruppenkommandant"},
    {"code": "ZK",   "label": "Zugskommandant"},
    {"code": "EL",   "label": "Einsatzleiter"},
    {"code": "TF",   "label": "Truppführer"},
    {"code": "TM",   "label": "Truppmann"},
    {"code": "JF",   "label": "Jugendfeuerwehr"},
]


def seed(db=None):
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        _upsert_roles(db)
        _upsert_qualifications(db)
        _upsert_alarm_types(db)
        _upsert_depts_and_vehicles(db)
        _upsert_task_suggestions(db)
        _upsert_default_messages(db)
        _upsert_lage_hints(db)
        db.commit()
        print("✓ Seed-Daten eingespielt.")
    finally:
        if close:
            db.close()


def _upsert_roles(db):
    for r in ROLES:
        obj = db.query(Role).filter(Role.code == r["code"]).first()
        if not obj:
            db.add(Role(**r))


def _upsert_qualifications(db):
    for q in QUALIFICATIONS:
        obj = db.query(Qualification).filter(Qualification.code == q["code"]).first()
        if not obj:
            db.add(Qualification(**q))


def _upsert_alarm_types(db):
    for a in ALARM_TYPES:
        obj = db.get(AlarmType, a["code"])
        if not obj:
            db.add(AlarmType(**a))
        else:
            for k, v in a.items():
                setattr(obj, k, v)


def _upsert_depts_and_vehicles(db):
    dept_map = {}
    for d in FIRE_DEPTS:
        obj = db.query(FireDept).filter(FireDept.slug == d["slug"]).first()
        if not obj:
            obj = FireDept(**d)
            db.add(obj)
            db.flush()
        dept_map[d["slug"]] = obj

    # Wolfurt own vehicles
    for v in WOLFURT_VEHICLES:
        existing = db.query(VehicleMaster).filter(VehicleMaster.code == v["code"]).first()
        if not existing:
            db.add(VehicleMaster(dept_id=dept_map["wolfurt"].id, **v))

    # Neighbor vehicles
    for slug, vehicles in NEIGHBOR_VEHICLES.items():
        dept = dept_map.get(slug)
        if dept is None:
            continue
        for v in vehicles:
            existing = db.query(VehicleMaster).filter(VehicleMaster.code == v["code"]).first()
            if not existing:
                db.add(VehicleMaster(dept_id=dept.id, **v))


def _upsert_task_suggestions(db):
    if db.query(TaskSuggestion).count() > 0:
        return
    for alarm_code, suggestions in TASK_SUGGESTIONS.items():
        for i, text in enumerate(suggestions):
            db.add(TaskSuggestion(alarm_type_code=alarm_code, text=text, display_order=i))


def _upsert_default_messages(db):
    if db.query(DefaultMessage).count() > 0:
        return
    for alarm_code, messages in DEFAULT_MESSAGES.items():
        for msg in messages:
            db.add(DefaultMessage(alarm_type_code=alarm_code, **msg))


def _upsert_lage_hints(db):
    existing = db.query(LageHint).count()
    if existing == 0:
        for i, text in enumerate(LAGE_HINTS):
            db.add(LageHint(text=text, display_order=i))


if __name__ == "__main__":
    seed()

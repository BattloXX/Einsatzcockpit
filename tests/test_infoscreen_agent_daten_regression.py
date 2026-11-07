"""Vollstaendige /daten-Snapshots, vor S2 mit dem unveraenderten Router erzeugt."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from app.models.incident import Incident
from app.models.major_incident import MajorIncident
from app.models.objekt import InfoscreenUrl
from app.routers.ui_infoscreen_alarm import infoscreen_daten
from tests.test_infoscreen_monitor import db_org, _token  # noqa: F401


@pytest.mark.parametrize("modus", ["idle", "alarm", "gsl"])
def test_daten_snapshot_vor_nach_s2(db_org, modus):
    db, org = db_org
    url = InfoscreenUrl(org_id=org.id, label="News", url="https://news.example", dwell_sec=20)
    db.add(url)
    db.flush()
    _token(db, org, url_ids=[url.id], zeigt_wetter=True)
    if modus in ("alarm", "gsl"):
        db.add(Incident(primary_org_id=org.id, alarm_type_code="F14", status="active",
                        started_at=datetime(2026, 11, 2, 12), address_street="Teststrasse",
                        address_no="1", address_city="Wolfurt"))
    if modus == "gsl":
        db.add(MajorIncident(org_id=org.id, name="Hochwasser", status="active",
                             started_at=datetime(2026, 11, 2, 11)))
    db.commit()
    actual = infoscreen_daten("mon-token", request=None, db=db)
    path = Path(__file__).parent / "data" / "infoscreen_agent_s2" / (modus + ".json")
    expected = json.loads(path.read_text())
    assert json.loads(json.dumps(actual)) == expected

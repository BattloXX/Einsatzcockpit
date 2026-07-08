"""Regression: Einsatz-PDF (render_incident_pdf) darf t.fahrzeug.display_label nach dem
Schliessen der Lade-Session rendern koennen. Ohne nested Eager-Loading von
VehicleMaster.dept scheiterte das mit DetachedInstanceError (Prod, Einsatz 193).
"""
import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import joinedload, sessionmaker


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(element, compiler, **kw):
    return "INTEGER"


from sqlalchemy.orm.exc import DetachedInstanceError

from app.core.tenant import set_tenant_context
from app.db import Base
from app.models.incident import Incident
from app.models.master import FireDept, VehicleMaster
from app.models.teilnahme import Teilnahme


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


def _seed(engine):
    s = sessionmaker(bind=engine)()
    set_tenant_context(s, None)
    org = FireDept(slug="w", name="Wolfurt", color="#f00", bos="Feuerwehr", short_code="WOL")
    s.add(org)
    s.flush()
    vm = VehicleMaster(dept_id=org.id, code="RLF", name="RLF-A")
    s.add(vm)
    s.flush()
    inc = Incident(primary_org_id=org.id, alarm_type_code="T1", status="active")
    s.add(inc)
    s.flush()
    s.add(Teilnahme(org_id=org.id, bezug_typ="einsatz", bezug_id=inc.id, fahrzeug_id=vm.id))
    s.commit()
    inc_id = inc.id
    s.close()
    return inc_id


def _load_teilnahmen(engine, inc_id, *, eager: bool):
    """Wie _load_pdf_context: eigene Session, danach geschlossen (Objekte detached)."""
    s = sessionmaker(bind=engine)()
    set_tenant_context(s, None)
    try:
        q = s.query(Teilnahme).filter(Teilnahme.bezug_typ == "einsatz", Teilnahme.bezug_id == inc_id)
        if eager:
            q = q.options(joinedload(Teilnahme.fahrzeug).joinedload(VehicleMaster.dept))
        return q.all()
    finally:
        s.close()


def test_display_label_ohne_eager_loading_scheitert(engine):
    inc_id = _seed(engine)
    teiln = _load_teilnahmen(engine, inc_id, eager=False)
    # dept ist nicht geladen -> Zugriff nach Session-Close wirft DetachedInstanceError
    with pytest.raises(DetachedInstanceError):
        _ = teiln[0].fahrzeug.display_label


def test_display_label_mit_eager_loading_ok(engine):
    inc_id = _seed(engine)
    teiln = _load_teilnahmen(engine, inc_id, eager=True)
    # Mit nested Eager-Loading (fahrzeug.dept) funktioniert der Zugriff nach Close.
    assert teiln[0].fahrzeug.display_label  # kein DetachedInstanceError

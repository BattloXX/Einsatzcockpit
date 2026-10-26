"""Phase 3: echte PDF-Auswahl, Rechte und vorhandene Stations-/Agentenkette."""

import io
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from pypdf import PdfReader, PdfWriter

from app.core.tenant import set_tenant_context
from app.models.gateway import Gateway, Printer, PrinterAgentRoute, PrintJob
from app.models.master import OrgSettings, SystemSettings
from app.models.user import User
from app.services import print_agent_service as agent
from app.services.print_artifact_service import artifact_url
from tests.conftest import TestingSession
from tests.gateway_helpers import CaptureSocket
from tests.test_dokumentviewer import dokument_pdf
from tests.test_dokumentviewer import dokumente as dokumente
from tests.test_katplan_k1b import login
from tests.test_katplan_k1b import umgebung as basis_umgebung  # noqa: F401


def post(client, path, **data):
    return client.post(path, json=data, headers={"X-CSRF-Token": client.cookies.get("ec_csrf", "")})


def referenz(info, index=0, pages="alle"):
    return {"url": f'/plaene/anhang/{info["dateien"][index]["uuid"]}?version_id={info["version_id"]}',
            "pages": pages}


def vorbereiten(client, info, pages="alle", **kwargs):
    return post(client, "/druck/viewer/vorbereiten", documents=[referenz(info, pages=pages)], **kwargs)


def pdf(response):
    assert response.status_code == 200, response.text[:1000]
    return PdfReader(io.BytesIO(response.content))


@pytest.mark.parametrize("pages,numbers", [("1", [1]), ("2–4", [2, 3, 4]), ("1,5,8", [1, 5, 8]),
                                         ("3–5,9,12", [3, 4, 5, 9, 12])])
def test_seiten_druckpdf_originalqualitaet(client, dokumente, pages, numbers):
    login(client, dokumente, "readonly")
    original = dokumente["dateien"][0]["path"].read_bytes()
    response = vorbereiten(client, dokumente, pages)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["page_count"] == len(numbers)
    result = client.get(data["pdf_url"])
    reader = pdf(result)
    assert [float(page.mediabox.width) for page in reader.pages] == [299 + n for n in numbers]
    assert [page.extract_text().splitlines()[0] for page in reader.pages] == [
        f"Unterlage - Seite {n}" for n in numbers]
    assert original == dokumente["dateien"][0]["path"].read_bytes()
    assert result.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'self'" in result.headers["content-security-policy"]
    download = client.get(data["pdf_url"] + "&download=true")
    assert download.headers["content-disposition"].startswith("attachment")
    assert client.get("/druck/viewer/drucker").json()["allowed"] is False


def test_zwei_dokumente_in_auswahlreihenfolge_und_bild(client, dokumente):
    login(client, dokumente, "readonly")
    response = post(client, "/druck/viewer/vorbereiten", documents=[
        referenz(dokumente, 1, "8"), referenz(dokumente, 0, "2–4"), referenz(dokumente, 3)])
    assert response.status_code == 200, response.text
    reader = pdf(client.get(response.json()["pdf_url"]))
    assert len(reader.pages) == 5
    assert [page.extract_text().splitlines()[0] for page in reader.pages[:4]] == [
        "Unterlage - Seite 8", "Unterlage - Seite 2", "Unterlage - Seite 3", "Unterlage - Seite 4"]
    assert reader.pages[4].images


@pytest.mark.parametrize("selection", ["0", "13", "5-2", "1,,3", "", "99999999999999999"])
def test_fehlerhafte_seitenauswahl(client, dokumente, selection):
    login(client, dokumente, "readonly")
    assert vorbereiten(client, dokumente, selection).status_code == 422


def test_berechtigungen_csrf_ssrf_und_aenderung(client, dokumente):
    login(client, dokumente, "readonly")
    assert client.post("/druck/viewer/vorbereiten", json={"documents": [referenz(dokumente)]}).status_code == 403
    for url in ("https://example.com/datei.pdf", "//localhost/etc/passwd", "/etc/passwd",
                "/plaene/anhang/egal?download=1"):
        assert post(client, "/druck/viewer/vorbereiten", documents=[{"url": url}]).status_code == 422
    assert post(client, "/druck/viewer/vorbereiten", documents=[
        {"url": f'/plaene/anhang/{dokumente["geheim"]}'}]).status_code == 404
    data = vorbereiten(client, dokumente).json()
    assert client.get(data["pdf_url"] + "kaputt").status_code == 410
    login(client, dokumente, "fremd")
    assert client.get(data["pdf_url"]).status_code == 404
    assert vorbereiten(client, dokumente).status_code == 404
    login(client, dokumente, "readonly")
    dokumente["dateien"][0]["path"].write_bytes(dokument_pdf(3))
    assert client.get(data["pdf_url"]).status_code == 409
    dokumente["dateien"][0]["path"].unlink()
    assert client.get(data["pdf_url"]).status_code == 404


def test_freigabe_und_modulrechte_werden_beim_pdfabruf_erneut_geprueft(client, dokumente):
    login(client, dokumente, "readonly")
    data = vorbereiten(client, dokumente).json()
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="katplan_module_enabled").one()
        flag.value = "false"
        db.commit()
    assert client.get(data["pdf_url"]).status_code == 404


def test_manifest_nur_fuer_ersteller_und_zeitlich_begrenzt(client, dokumente, monkeypatch):
    from itsdangerous.timed import TimestampSigner

    login(client, dokumente, "readonly")
    data = vorbereiten(client, dokumente).json()
    login(client, dokumente, "org_admin")
    assert client.get(data["pdf_url"]).status_code == 404
    login(client, dokumente, "readonly")
    timestamp = TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: timestamp(self) + 901)
    assert client.get(data["pdf_url"]).status_code == 410


def test_objektdokument_und_plan_im_selben_paket(client, dokumente, tmp_path, monkeypatch):
    from app.config import settings
    from app.models.objekt import Objekt, ObjektDokument

    monkeypatch.setattr(settings, "OBJEKT_MEDIA_DIR", str(tmp_path / "objekte"))
    (tmp_path / "objekte").mkdir()
    (tmp_path / "objekte/test.pdf").write_bytes(dokument_pdf())
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").first()
        old = flag.value if flag else None
        if flag is None:
            flag = SystemSettings(key="objekt_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().objekt_module_enabled = True
        objekt = Objekt(org_id=dokumente["org_id"], name="Drucktest")
        db.add(objekt)
        db.flush()
        dok = ObjektDokument(org_id=objekt.org_id, objekt_id=objekt.id,
                            dateiname_original="test.pdf", pfad="test.pdf", seitenzahl=12)
        db.add(dok)
        db.commit()
        url = f"/objekt-medien/dokument/{dok.id}/original"
    try:
        login(client, dokumente, "readonly")
        response = post(client, "/druck/viewer/vorbereiten", documents=[
            {"url": url, "pages": "8"}, referenz(dokumente, pages="1")])
        assert response.status_code == 200, response.text
        assert [float(p.mediabox.width) for p in pdf(client.get(response.json()["pdf_url"])).pages] == [307, 300]
        login(client, dokumente, "fremd")
        assert post(client, "/druck/viewer/vorbereiten", documents=[{"url": url}]).status_code == 404
    finally:
        with TestingSession() as db:
            set_tenant_context(db, None)
            flag = db.query(SystemSettings).filter_by(key="objekt_module_enabled").one()
            if old is None:
                db.delete(flag)
            else:
                flag.value = old
            db.commit()


def test_generierter_plan_bleibt_abrufbar_trotz_pdf_metadaten(client, dokumente, monkeypatch):
    from app.services import katplan_pdf_service

    login(client, dokumente, "readonly")
    original = katplan_pdf_service.render_katplan_pdf
    render_times = []

    def wechselnde_metadaten(*args, **kwargs):
        render_times.append(kwargs.get("druckzeit"))
        writer = PdfWriter(clone_from=io.BytesIO(original(*args, **kwargs)))
        writer.add_metadata({"/CreationDate": str(uuid4())})
        stream = io.BytesIO()
        writer.write(stream)
        return stream.getvalue()

    monkeypatch.setattr(katplan_pdf_service, "render_katplan_pdf", wechselnde_metadaten)
    response = post(client, "/druck/viewer/vorbereiten", documents=[{
        "url": f'/plaene/{dokumente["plan_id"]}/pdf?version_id={dokumente["version_id"]}', "pages": "1"}])
    assert response.status_code == 200, response.text
    assert len(pdf(client.get(response.json()["pdf_url"])).pages) == 1
    assert render_times[0] is not None and render_times[0] == render_times[1]


def a3_datei(info):
    source = PdfReader(io.BytesIO(dokument_pdf(1)))
    source.pages[0].scale_to(841.89, 1190.551)
    writer = PdfWriter()
    writer.add_page(source.pages[0])
    stream = io.BytesIO()
    writer.write(stream)
    info["dateien"][0]["path"].write_bytes(stream.getvalue())


def test_a3_ohne_beschnitt_und_explizite_umwandlung(client, dokumente):
    login(client, dokumente, "readonly")
    a3_datei(dokumente)
    data = vorbereiten(client, dokumente).json()
    assert data["media"] == ["A3"]
    page = pdf(client.get(data["pdf_url"])).pages[0]
    assert float(page.mediabox.width) == pytest.approx(841.89)
    assert page.cropbox == page.mediabox
    assert "Unterlage - Seite 1" in page.extract_text()
    data = vorbereiten(client, dokumente, format="A4", orientation="landscape").json()
    assert "Formatumwandlung" in data["note"] and data["media"] == ["A4"]
    page = pdf(client.get(data["pdf_url"])).pages[0]
    assert float(page.mediabox.width) == pytest.approx(841.89)
    assert float(page.mediabox.height) == pytest.approx(595.276)
    assert "Unterlage - Seite 1" in page.extract_text()


@pytest.fixture
def station(dokumente, monkeypatch):
    from app.routers import ws

    async def notify(*args, **kwargs):
        return None

    monkeypatch.setattr(ws, "notify_agents_job_available", notify)
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="gateway_module_enabled").first()
        old = flag.value if flag else None
        if not flag:
            flag = SystemSettings(key="gateway_module_enabled")
            db.add(flag)
        flag.value = "true"
        db.query(OrgSettings).filter_by(org_id=dokumente["org_id"]).one().gateway_module_enabled = True
        now = datetime.now(UTC).replace(tzinfo=None)
        token_hash = uuid4().hex
        gateway = Gateway(org_id=dokumente["org_id"], name="Windows Station", kind="windows_agent",
                          device_token_hash=token_hash, status="online", last_seen_at=now,
                          agent_connection_token_hash=token_hash, agent_worker_id="test-worker",
                          agent_connection_id=uuid4().hex, agent_presence_expires_at=now + timedelta(seconds=90))
        db.add(gateway)
        db.flush()
        printer = Printer(org_id=dokumente["org_id"], gateway_id=gateway.id, name="Stationsdrucker",
                          uri="agent://station", aktiv=True, capabilities={"media": ["A4", "A3"]})
        db.add(printer)
        db.flush()
        route = PrinterAgentRoute(org_id=printer.org_id, gateway_id=gateway.id, printer_id=printer.id,
                                  local_queue="Test", ready=True,
                                  options={"orientation": "landscape", "scaling": "actual"},
                                  readiness={"state": "ready", "checked_at": now.isoformat()})
        db.add(route)
        db.commit()
        result = {"printer_id": printer.id, "gateway_id": gateway.id, "route_id": route.id}
    yield result
    with TestingSession() as db:
        set_tenant_context(db, None)
        flag = db.query(SystemSettings).filter_by(key="gateway_module_enabled").one()
        if old is None:
            db.delete(flag)
        else:
            flag.value = old
        db.commit()


def jobdaten(client, info, station, **kwargs):
    return {"token": vorbereiten(client, info, format="A3").json()["token"],
            "printer_id": station["printer_id"], "copies": 2, "request_id": str(uuid4()), **kwargs}


def test_station_vorhandener_job_agent_status_idempotenz_und_artifact(client, dokumente, station):
    login(client, dokumente, "org_admin")
    assert client.get("/druck/viewer/drucker").json()["printers"][0]["ready"] is True
    data = jobdaten(client, dokumente, station)
    response = post(client, "/druck/viewer/auftrag", **data)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "queued" and job["confirmed"] is False
    assert post(client, "/druck/viewer/auftrag", **data).json()["job_id"] == job["job_id"]
    assert post(client, "/druck/viewer/auftrag", **{**data, "copies": 3}).status_code == 409
    with TestingSession() as db:
        set_tenant_context(db, None)
        stored = db.get(PrintJob, job["job_id"])
        assert db.query(PrintJob).filter_by(printer_id=station["printer_id"]).count() == 1
        assert stored.options["media"] == "A3" and stored.options["copies"] == 2
        url = artifact_url(stored)
        gateway = db.get(Gateway, station["gateway_id"])
        grant = agent.lease_jobs(db, gateway, 1)[0]
        assert set(grant["options"]) == {"copies", "duplex", "media"}
        assert grant["route"]["options"] == {"orientation": "auto", "scaling": "fit", "collate": True}
        assert db.get(PrinterAgentRoute, station["route_id"]).options["scaling"] == "actual"
        assert agent.print_intent(db, gateway, stored.id, grant["lease_token"]) == (True, None)
        agent.apply_agent_status(db, gateway, stored.id, grant["lease_token"], "printing", "spooled", "win:1", None)
    assert len(pdf(client.get(url)).pages) == 12
    status_url = f'/druck/viewer/auftrag/{job["job_id"]}'
    assert client.get(status_url).json()["confirmed"] is False
    with TestingSession() as db:
        set_tenant_context(db, None)
        gateway = db.get(Gateway, station["gateway_id"])
        agent.apply_agent_status(db, gateway, job["job_id"], grant["lease_token"], "done", "spooled", "win:1", None)
    assert client.get(status_url).json()["confirmed"] is True
    login(client, dokumente, "readonly")
    assert client.get(status_url).status_code == 403
    assert post(client, "/druck/viewer/auftrag", **data).status_code == 403
    login(client, dokumente, "org_admin")
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.query(User).filter_by(username=dokumente["users"]["org_admin"]).one().active = False
        db.commit()
    assert client.get(url).status_code == 422  # Signatur ersetzt keine aktuellen Dokumentrechte.


def test_station_offline_kein_job_und_mischformat_nur_explizit(client, dokumente, station):
    login(client, dokumente, "org_admin")
    data = {**jobdaten(client, dokumente, station), "token": vorbereiten(client, dokumente).json()["token"]}
    assert post(client, "/druck/viewer/auftrag", **data).status_code == 422
    with TestingSession() as db:
        set_tenant_context(db, None)
        db.get(PrinterAgentRoute, station["route_id"]).ready = False
        db.commit()
    response = post(client, "/druck/viewer/auftrag", **jobdaten(client, dokumente, station))
    assert response.status_code == 409 and "offline" in response.json()["detail"]
    with TestingSession() as db:
        set_tenant_context(db, None)
        assert db.query(PrintJob).filter_by(printer_id=station["printer_id"]).count() == 0


def test_station_original_a3_und_fremder_drucker(client, dokumente, station):
    login(client, dokumente, "org_admin")
    a3_datei(dokumente)
    data = {**jobdaten(client, dokumente, station), "token": vorbereiten(client, dokumente).json()["token"]}
    assert post(client, "/druck/viewer/auftrag", **{**data, "printer_id": 99999999}).status_code == 404
    response = post(client, "/druck/viewer/auftrag", **data)
    assert response.status_code == 200, response.text
    with TestingSession() as db:
        set_tenant_context(db, None)
        job = db.get(PrintJob, response.json()["job_id"])
        assert job.options["media"] == "A3"
        url = artifact_url(job)
    page = pdf(client.get(url)).pages[0]
    assert float(page.mediabox.width) == pytest.approx(841.89)
    # Die äußere Transformation hält alle vier Original-Seitenecken innerhalb
    # des Sicherheitsrandes. Die A3-Medienbox selbst wird nicht verkleinert.
    matrix = next(args for args, operator in page.get_contents().operations if operator == b"cm")
    a, b, c, d, e, f = map(float, matrix)
    corners = [(a * x + c * y + e, b * x + d * y + f)
               for x in (0, 841.89) for y in (0, 1190.551)]
    assert all(17.99 <= x <= 841.89 - 17.99 and 17.99 <= y <= 1190.551 - 17.99 for x, y in corners)


def test_linux_push_verwendet_selbe_auswahl_und_nur_clientoptionen(client, dokumente, station, monkeypatch):
    from app.routers import ws
    from app.services import gateway_routing as routing
    from app.services import linux_print_service

    monkeypatch.setattr(ws, "dispatch_print_job", partial(linux_print_service.dispatch, wait_seconds=0))
    with TestingSession() as db:
        set_tenant_context(db, None)
        gateway = db.get(Gateway, station["gateway_id"])
        gateway.kind = "linux"
        db.get(PrinterAgentRoute, station["route_id"]).aktiv = False
        db.get(Printer, station["printer_id"]).status = {
            "reachable": True, "checked_at": datetime.now(UTC).isoformat()}
        token_hash = gateway.device_token_hash
        db.commit()
    connection = routing.begin("linux", dokumente["org_id"], station["gateway_id"], token_hash)
    socket = CaptureSocket()
    routing.attach(connection, token_hash, socket)
    try:
        login(client, dokumente, "org_admin")
        response = post(client, "/druck/viewer/auftrag", **jobdaten(client, dokumente, station))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "sent" and response.json()["confirmed"] is False
        sent = [message["payload"] for message in socket.sent]
        assert len(sent) == 1
        assert sent[0]["options"] == {"copies": 2, "duplex": "off", "media": "A3"}
        assert len(pdf(client.get(sent[0]["artifact_url"])).pages) == 12
    finally:
        routing.detach(connection)
        routing.end(connection)

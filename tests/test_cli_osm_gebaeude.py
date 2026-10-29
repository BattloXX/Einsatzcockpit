"""CLI-Aufrufe gegen synthetischen lokalen OSM-Bestand."""

import json
import sys

import pytest

from app.cli import main
from tests.test_osm_gebaeude_import import osm_umgebung as osm_umgebung


def test_status_import_aktivieren(osm_umgebung, monkeypatch, capsys):
    _, pbf, _ = osm_umgebung

    def aufruf(*args):
        monkeypatch.setattr(sys, "argv", ["app.cli", *args])
        main()
        return json.loads(capsys.readouterr().out)

    assert aufruf("osm-gebaeude-status")[0]["aktiv"] is None
    pbf({i: [[47 + i * .001, 9], [47.0001 + i * .001, 9], [47 + i * .001, 9.0001]] for i in range(1, 11)})
    erster = aufruf("osm-gebaeude-import", "--quelle", "region", "--erzwingen")
    assert erster["status"] == "aktiv" and erster["anzahl"] == 10
    assert aufruf("osm-gebaeude-import")["ergebnis"] == "uebersprungen"
    pbf()
    pruefung = aufruf("osm-gebaeude-import", "--erzwingen")
    assert pruefung["status"] == "pruefung"
    assert aufruf("osm-gebaeude-aktivieren", str(pruefung["id"]))["status"] == "aktiv"
    assert aufruf("osm-gebaeude-status")[0]["aktiv"]["id"] == pruefung["id"]


def test_unbekannte_quelle_und_downloadfehler(osm_umgebung, monkeypatch, capsys):
    _, _, zustand = osm_umgebung
    monkeypatch.setattr(sys, "argv", ["app.cli", "osm-gebaeude-import", "--quelle", "fehlt"])
    with pytest.raises(SystemExit) as fehler:
        main()
    assert fehler.value.code == 2
    capsys.readouterr()
    zustand["status"] = 503
    monkeypatch.setattr(sys, "argv", ["app.cli", "osm-gebaeude-import"])
    with pytest.raises(SystemExit) as fehler:
        main()
    assert fehler.value.code == 1
    assert json.loads(capsys.readouterr().out)["fehler_code"] == "OSM_DOWNLOAD_HTTP"


def test_ohne_unterbefehl_zeigt_hilfe(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["app.cli"])
    with pytest.raises(SystemExit) as fehler:
        main()
    assert fehler.value.code == 1
    assert "osm-gebaeude-import" in capsys.readouterr().out

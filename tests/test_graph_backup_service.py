"""PR D: Microsoft-Graph-Backup (SharePoint/OneDrive) + remote_backup_service-Dispatch."""
from pathlib import Path

import httpx
import pytest

from app.core.crypto import encrypt_secret
from app.models.org_backup import OrgBackupConfig
from app.services import graph_backup_service as gbs
from app.services import remote_backup_service as rbs


def _ziel(**kw):
    base = dict(tenant="t1", client_id="c1", secret="s1", drive_id="drv", folder="Backups/EC")
    base.update(kw)
    return gbs.GraphZiel(**base)


# ── graph_backup_service (httpx.MockTransport) ────────────────────────────────

def test_graph_upload_upload_session(tmp_path):
    datei = tmp_path / "org-backup-1-x.zip"
    datei.write_bytes(b"A" * 100)
    ereignisse = {"session": 0, "puts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("createUploadSession"):
            ereignisse["session"] += 1
            return httpx.Response(200, json={"uploadUrl": "https://upload.example/abc"})
        if request.method == "PUT":
            ereignisse["puts"].append(request.headers.get("Content-Range"))
            return httpx.Response(201, json={})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gbs.upload(_ziel(), datei, client=client, token="tok")
    assert ereignisse["session"] == 1
    assert ereignisse["puts"] == ["bytes 0-99/100"]


def test_graph_liste_filtert_praefix():
    def handler(request):
        return httpx.Response(200, json={"value": [
            {"name": "org-backup-1-20260101-000000Z.zip"},
            {"name": "org-backup-1-20260102-000000Z.zip"},
            {"name": "fremd.txt"},
        ]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    namen = gbs.liste(_ziel(), "org-backup-1-", client=client, token="tok")
    assert namen == [
        "org-backup-1-20260101-000000Z.zip",
        "org-backup-1-20260102-000000Z.zip",
    ]


def test_graph_loesche_ok():
    gesehen = []

    def handler(request):
        gesehen.append((request.method, request.url.path))
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gbs.loesche(_ziel(), "org-backup-1-alt.zip", client=client, token="tok")
    assert gesehen and gesehen[0][0] == "DELETE"
    assert "org-backup-1-alt.zip" in gesehen[0][1]


# ── remote_backup_service: Config + Dispatch ──────────────────────────────────

def test_config_pruefen_graph():
    cfg = rbs.RemoteConfig(protocol="graph", host="", port=0, user="", password="", key="",
                           path="", graph_tenant="t", graph_client="c", graph_secret="s",
                           graph_drive_id="d")
    rbs.config_pruefen(cfg)  # vollstaendig -> ok
    with pytest.raises(ValueError):
        rbs.config_pruefen(rbs.RemoteConfig(protocol="graph", host="", port=0, user="",
                                            password="", key="", path="", graph_tenant="t"))


def test_config_aus_org_graph_entschluesselt():
    row = OrgBackupConfig(org_id=1, protocol="graph", graph_tenant_id="t", graph_client_id="c",
                          graph_client_secret_enc=encrypt_secret("geheim"),
                          graph_drive_id="drv", graph_folder="F")
    cfg = rbs.config_aus_org(row)
    assert cfg.protocol == "graph"
    assert cfg.graph_secret == "geheim"
    assert cfg.graph_drive_id == "drv" and cfg.graph_folder == "F"


def test_upload_dispatch_graph(tmp_path, monkeypatch):
    hochgeladen = []
    monkeypatch.setattr("app.services.graph_backup_service.upload",
                        lambda ziel, pfad, **kw: hochgeladen.append((ziel.drive_id, Path(pfad).name)))
    cfg = rbs.RemoteConfig(protocol="graph", host="", port=0, user="", password="", key="",
                           path="", graph_tenant="t", graph_client="c", graph_secret="s",
                           graph_drive_id="drv", graph_folder="F")
    datei = tmp_path / "org-backup-1-x.zip"
    datei.write_bytes(b"z")
    rbs.upload(cfg, [datei], tmp_path)
    assert hochgeladen == [("drv", "org-backup-1-x.zip")]


def test_prune_remote_graph(monkeypatch):
    monkeypatch.setattr("app.services.graph_backup_service.liste",
                        lambda ziel, praefix, **kw: [f"org-backup-1-2026010{i}-000000Z.zip" for i in range(1, 5)])
    geloescht = []
    monkeypatch.setattr("app.services.graph_backup_service.loesche",
                        lambda ziel, name, **kw: geloescht.append(name))
    cfg = rbs.RemoteConfig(protocol="graph", host="", port=0, user="", password="", key="",
                           path="", graph_tenant="t", graph_client="c", graph_secret="s",
                           graph_drive_id="drv")
    weg = rbs.prune_remote(cfg, "org-backup-1-", keep=2)
    assert weg == ["org-backup-1-20260101-000000Z.zip", "org-backup-1-20260102-000000Z.zip"]
    assert set(geloescht) == set(weg)

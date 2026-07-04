"""SOAP-Client für die LIS/IPR-Schnittstelle (Intergraph.Emea.Pr, SOAP 1.1 / WCF).

Reverse-engineered aus LIS_IPR_Schnittstellen_Dokumentation.md. Kein zeep-Einsatz —
die Envelopes werden als Templates gebaut, Antworten generisch (namespace-tolerant)
in verschachtelte dicts/Listen umgewandelt. httpx dekomprimiert gzip automatisch.

Wichtig: Diese Doku basiert auf einem einmaligen Netzwerkmitschnitt. Einzelne
Feld-/Parameternamen (v.a. bei GetOperationUnits) sind nicht 100% verifiziert —
bei Abweichungen im Live-Betrieb hier nachschärfen (siehe Kommentare unten).
"""
from __future__ import annotations

import email
import gzip
import hashlib
import logging
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Callable

import httpx

logger = logging.getLogger("einsatzleiter.lis.client")

_NS_BASE = "http://services.intergraph.com/Emea/Pr/2011/03"
_NS_CORE = f"{_NS_BASE}/Core"
_NS_OPERATION = f"{_NS_BASE}/Operation"
_NS_TYPES = f"{_NS_BASE}/Types"
_NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
_NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

# SOAPAction-Header sind IMMER {_NS_BASE}/{Service}/{Operation} — unabhängig vom
# Body-Namespace des jeweiligen Elements (siehe Doku Abschnitt 4, Fußnote 1).
_ACTION_LOGIN = f"{_NS_BASE}/CoreService/Login"
_ACTION_ADD_SESSION_ENTRIES = f"{_NS_BASE}/CoreService/AddSessionEntries"
_ACTION_SELECT_OPERATION = f"{_NS_BASE}/CoreService/SelectOperation"
_ACTION_GET_OPERATIONS = f"{_NS_BASE}/OperationService/GetOperationsInRange"
_ACTION_GET_TASKS = f"{_NS_BASE}/OperationService/GetTasks"
_ACTION_GET_OPERATION_UNITS = f"{_NS_BASE}/OperationService/GetOperationUnits"
_ACTION_GET_DOCUMENTS = f"{_NS_BASE}/OperationService/GetDocumentsByOperationId"
_ACTION_DOWNLOAD_ATTACHMENT = f"{_NS_BASE}/CoreService/DownloadAttachment"


class LisClientError(Exception):
    """SOAP-Fault oder Transportfehler bei der Kommunikation mit dem LIS."""


class LisAuthError(LisClientError):
    """Login fehlgeschlagen oder Session abgelaufen (auch nach Retry)."""


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _elem_to_value(elem: ET.Element) -> Any:
    children = list(elem)
    if not children:
        nil = elem.get(f"{{{_NS_XSI}}}nil")
        if nil and nil.lower() == "true":
            return None
        text = (elem.text or "").strip()
        return text if text else None
    result: dict[str, Any] = {}
    for child in children:
        name = _local(child.tag)
        value = _elem_to_value(child)
        if name in result:
            if not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(value)
        else:
            result[name] = value
    return result


def _find_by_local(root: ET.Element, local_name: str) -> ET.Element | None:
    for elem in root.iter():
        if _local(elem.tag) == local_name:
            return elem
    return None


def _find_fault(root: ET.Element) -> str | None:
    fault = _find_by_local(root, "Fault")
    if fault is None:
        return None
    reason = _find_by_local(fault, "Text")  # SOAP 1.2 style, meist nicht genutzt hier
    if reason is not None and reason.text:
        return reason.text
    faultstring = _find_by_local(fault, "faultstring")
    if faultstring is not None and faultstring.text:
        return faultstring.text
    return "Unbekannter SOAP-Fault"


def _result_list(root: ET.Element, result_tag: str) -> list[dict]:
    """Extrahiert die Item-Liste aus einem `<...Result>`-Element.

    Echter Mitschnitt (Capture 2026-07-04, Testeinsatz LIS) zeigt: `GetOperationsResult`
    ist als `Tuple<List<Operation>, int>` serialisiert — genau zwei Kinder `m_Item1`
    (die eigentliche Liste) und `m_Item2` (Gesamtanzahl, für Pagination), NICHT ein
    flaches Array direkt unter `<Result>`. Ohne Entpacken von `m_Item1` würde jedes
    Ergebnis fälschlich als `{"Operation": {...}}` statt der Operation direkt geliefert
    — `op.get("Id")` liefert dann immer None und der komplette Sync bricht lautlos ab.
    GetTasks/GetOperationUnits/GetDocumentsByOperationId haben keine range/count/
    startIndex-Parameter und liefern vermutlich (unbestätigt) ein flaches Array ohne
    Tupel-Wrapper — daher bleibt der direkte-Kinder-Fallback erhalten.
    """
    result = _find_by_local(root, result_tag)
    if result is None:
        return []
    children = list(result)
    container = next((c for c in children if _local(c.tag) == "m_Item1"), None)
    if container is not None:
        children = list(container)
    items = []
    for child in children:
        val = _elem_to_value(child)
        if isinstance(val, dict):
            items.append(val)
    return items


def _result_dict(root: ET.Element, result_tag: str) -> dict | None:
    result = _find_by_local(root, result_tag)
    if result is None:
        return None
    val = _elem_to_value(result)
    return val if isinstance(val, dict) else None


class LisClient:
    """Ein LIS-Client pro Organisation/Session (nicht thread-/task-übergreifend teilen)."""

    def __init__(
        self, base_url: str, site: str, username: str, password: str, timeout: float = 20.0,
        on_exchange: Callable[[str, str, bytes, bytes], None] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.site = site or "LIS"
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session_id: str = str(uuid.uuid4())
        self._logged_in = False
        # Aus LoginResult.User.Id — wird für add_session_entries() gebraucht.
        self.user_id: str | None = None
        # Diagnose-Hook: wird nach jedem SOAP-Austausch mit (url, soap_action,
        # request_bytes, response_bytes) aufgerufen — siehe lis_capture.py.
        # Rein lesend/beobachtend, hat keinen Einfluss auf den normalen Ablauf.
        self._on_exchange = on_exchange

    # ── Login / Session ────────────────────────────────────────────────────
    async def login(self) -> None:
        password_hash = hashlib.sha1(self.password.encode("utf-8")).hexdigest()
        body = (
            f'<Login xmlns="{_NS_CORE}">'
            f"<site>{_xml_escape(self.site)}</site>"
            f"<application>Cockpit</application>"
            f"<name>{_xml_escape(self.username)}</name>"
            f"<password>{password_hash}</password>"
            f'<clientHash i:nil="true" xmlns:i="{_NS_XSI}"/>'
            f"</Login>"
        )
        try:
            root = await self._post(
                f"{self.base_url}/CoreService.svc",
                _ACTION_LOGIN,
                body,
                retry_on_auth=False,
            )
        except LisClientError as exc:
            self._logged_in = False
            raise LisAuthError(f"LIS-Login fehlgeschlagen: {exc}") from exc
        self._logged_in = True

        login_result = _result_dict(root, "LoginResult") or {}
        user = login_result.get("User") or {}
        self.user_id = user.get("Id")
        if self.user_id:
            # SelectOperation allein reicht NICHT gegen die NullReferenceException bei
            # GetTasks (verifiziert: SelectOperation liefert 200 OK, GetTasks faultet
            # trotzdem weiterhin). Ein echter Mitschnitt zeigt zusätzlich einen
            # AddSessionEntries-Aufruf mit UserId/UserName früh in der Session — die
            # fehlschlagende Methode heißt server-seitig "SetRelatedOrganizations",
            # was auf eine User-basierte Organisations-Auflösung hindeutet. Best-effort:
            # Fehler hier loggen statt die Anmeldung selbst scheitern zu lassen.
            try:
                await self.add_session_entries({"UserId": self.user_id, "UserName": self.username})
            except LisClientError:
                logger.exception("LIS: AddSessionEntries nach Login fehlgeschlagen")

    async def add_session_entries(self, entries: dict[str, str]) -> None:
        """Schreibt Key/Value-Paare in die Server-Session (CoreService.svc/AddSessionEntries).

        Siehe Docstring von login() — wird automatisch mit UserId/UserName nach jedem
        Login aufgerufen, als (noch unbestätigter, aber am ehesten passender) Kandidat
        für die Ursache der GetTasks-NullReferenceException.
        """
        entries_xml = "".join(
            f"<a:SessionEntry><a:Key>{_xml_escape(k)}</a:Key>"
            f'<a:Value i:type="b:string" xmlns:b="http://www.w3.org/2001/XMLSchema">{_xml_escape(v)}</a:Value>'
            f"</a:SessionEntry>"
            for k, v in entries.items()
        )
        body = (
            f'<AddSessionEntries xmlns="{_NS_CORE}">'
            f"{self._site_session_xml()}"
            f'<sessionEntries xmlns:a="{_NS_TYPES}" xmlns:i="{_NS_XSI}">'
            f"{entries_xml}"
            f"</sessionEntries>"
            f"</AddSessionEntries>"
        )
        await self._post(
            f"{self.base_url}/CoreService.svc",
            _ACTION_ADD_SESSION_ENTRIES,
            body,
        )

    async def select_operation(self, organization_id: str, operation_id: str | None = None) -> None:
        """Setzt den Organisations-/Einsatzkontext der Session (CoreService.svc/SelectOperation).

        Ein echter Mitschnitt eines funktionierenden Referenz-Clients zeigt: ohne diesen
        Aufruf (einmalig direkt nach dem Login, bevor irgendein GetTasks erfolgt) wirft der
        Server bei GetTasks eine NullReferenceException
        (SessionData.get_OrganizationId() ist dann null) — GetOperationsInRange/
        GetOperationUnits funktionieren auch ohne SelectOperation, weil sie organizationId
        explizit als Parameter mitschicken, GetTasks aber nicht. Der Referenz-Client ruft
        SelectOperation immer mit operationId/operationUnitId=nil und nur organizationId
        gesetzt auf — genau dieses Muster wird hier übernommen.

        WICHTIG: In der Praxis (Capture 2026-07-04, zweiter Testlauf) reichte
        SelectOperation allein NICHT — GetTasks faultete trotzdem weiter. Siehe
        add_session_entries() für den zusätzlichen, noch unbestätigten Fix-Kandidaten.
        """
        await self._ensure_login()
        op_id_xml = (
            f"<operationId>{_xml_escape(operation_id)}</operationId>"
            if operation_id else
            f'<operationId i:nil="true" xmlns:i="{_NS_XSI}"/>'
        )
        body = (
            f'<SelectOperation xmlns="{_NS_CORE}">'
            f"{self._site_session_xml()}"
            f"{op_id_xml}"
            f'<operationUnitId i:nil="true" xmlns:i="{_NS_XSI}"/>'
            f"<organizationId>{_xml_escape(organization_id)}</organizationId>"
            f"</SelectOperation>"
        )
        await self._post(
            f"{self.base_url}/CoreService.svc",
            _ACTION_SELECT_OPERATION,
            body,
        )

    def _site_session_xml(self) -> str:
        return (
            f'<siteSession xmlns:a="{_NS_TYPES}" xmlns:i="{_NS_XSI}">'
            f"<a:Language>de-DE</a:Language>"
            f"<a:SessionId>{self.session_id}</a:SessionId>"
            f"<a:Site>{_xml_escape(self.site)}</a:Site>"
            f"</siteSession>"
        )

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.login()

    async def _post(
        self, url: str, soap_action: str, body_xml: str, *, retry_on_auth: bool = True,
    ) -> ET.Element:
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{_NS_SOAP}"><s:Body>{body_xml}</s:Body></s:Envelope>'
        )
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "Accept-Encoding": "gzip, deflate",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, content=envelope.encode("utf-8"), headers=headers)
        except httpx.HTTPError as exc:
            raise LisClientError(f"Transportfehler bei {url}: {exc}") from exc

        if self._on_exchange:
            try:
                self._on_exchange(url, soap_action, envelope.encode("utf-8"), resp.content)
            except Exception:
                logger.exception("on_exchange-Hook fehlgeschlagen (wird ignoriert)")

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise LisClientError(f"Ungültige SOAP-Antwort von {url}: {exc}") from exc

        fault = _find_fault(root)
        if fault:
            if retry_on_auth and "unauthoriz" in fault.lower():
                logger.info("LIS-Session abgelaufen, re-login und Retry")
                self._logged_in = False
                self.session_id = str(uuid.uuid4())
                await self.login()
                return await self._post(url, soap_action, body_xml, retry_on_auth=False)
            raise LisClientError(f"LIS SOAP Fault: {fault}")
        return root

    # ── OperationService.svc ────────────────────────────────────────────────
    async def get_operations_in_range(
        self,
        organization_id: str,
        operation_filter: str = "ActiveParticipation",
        count: int = 50,
        start_index: int = 0,
    ) -> list[dict]:
        """operation_filter: ActiveParticipation | LastDay | LastMonth | Planned"""
        await self._ensure_login()
        body = (
            f'<GetOperations xmlns="{_NS_OPERATION}">'
            f"{self._site_session_xml()}"
            f"<operationFilter>{operation_filter}</operationFilter>"
            f"<organizationId>{_xml_escape(organization_id)}</organizationId>"
            f'<range xmlns:a="{_NS_TYPES}"><a:Count>{count}</a:Count><a:StartIndex>{start_index}</a:StartIndex></range>'
            f'<filter xmlns:i="{_NS_XSI}" i:nil="true"/>'
            f"</GetOperations>"
        )
        root = await self._post(
            f"{self.base_url}/OperationService.svc",
            _ACTION_GET_OPERATIONS,
            body,
        )
        return _result_list(root, "GetOperationsResult")

    async def get_tasks(self, operation_id: str) -> list[dict]:
        await self._ensure_login()
        body = (
            f'<GetTasks xmlns="{_NS_OPERATION}">'
            f"{self._site_session_xml()}"
            f"<operationId>{_xml_escape(operation_id)}</operationId>"
            f"</GetTasks>"
        )
        root = await self._post(
            f"{self.base_url}/OperationService.svc",
            _ACTION_GET_TASKS,
            body,
        )
        return _result_list(root, "GetTasksResult")

    async def get_operation_units(self, organization_id: str, operation_id: str) -> list[dict]:
        """Fahrzeuge/Einheiten EINES Einsatzes mit Live-Status (siehe Doku Abschnitt 7.1).

        organizationId wird zusätzlich zu operationId mitgeschickt, da die Doku hier
        uneinheitlich ist (Tabelle 4.2 nennt nur organizationId, Abschnitt 7.1 "mit
        operationId") — im Zweifel sendet der Server überzählige Parameter meist
        ignoriert statt einen Fault zu werfen.
        """
        await self._ensure_login()
        body = (
            f'<GetOperationUnits xmlns="{_NS_OPERATION}">'
            f"{self._site_session_xml()}"
            f"<organizationId>{_xml_escape(organization_id)}</organizationId>"
            f"<operationId>{_xml_escape(operation_id)}</operationId>"
            f"</GetOperationUnits>"
        )
        root = await self._post(
            f"{self.base_url}/OperationService.svc",
            _ACTION_GET_OPERATION_UNITS,
            body,
        )
        return _result_list(root, "GetOperationUnitsResult")

    async def get_documents_by_operation_id(
        self, operation_id: str, maximum_distance: int = 100,
    ) -> list[dict]:
        await self._ensure_login()
        body = (
            f'<GetDocumentsByOperationId xmlns="{_NS_OPERATION}">'
            f"{self._site_session_xml()}"
            f"<operationId>{_xml_escape(operation_id)}</operationId>"
            f"<maximumDistance>{maximum_distance}</maximumDistance>"
            f"</GetDocumentsByOperationId>"
        )
        root = await self._post(
            f"{self.base_url}/OperationService.svc",
            _ACTION_GET_DOCUMENTS,
            body,
        )
        return _result_list(root, "GetDocumentsByOperationIdResult")

    async def download_document(self, document_id: str, entity: str = "PR_OPERATION") -> bytes:
        """Lädt den Binärinhalt eines Dokuments (MTOM/XOP, siehe Doku Abschnitt 4.5).

        Live-Fehler (2026-07-04): Die Doku behauptet, das Body-Element heiße
        DownloadAttachment (Core-Namespace), während SOAPAction/URL auf
        OperationService/DownloadDocument zeigen — das führt live zu einem
        WCF-ContractFilter-Mismatch ("mismatched Actions between sender and
        receiver"). Alle anderen bestätigten Core-Namespace-Operationen (Login,
        SelectOperation, AddSessionEntries) laufen konsistent über CoreService.svc
        mit SOAPAction = Body-Elementname — nach demselben Muster gehört
        DownloadAttachment ebenfalls zu CoreService.svc, nicht OperationService.svc.
        """
        await self._ensure_login()
        body = (
            f'<DownloadAttachment xmlns="{_NS_CORE}">'
            f"{self._site_session_xml()}"
            f"<id>{_xml_escape(document_id)}</id>"
            f"<entity>{_xml_escape(entity)}</entity>"
            f"<zipCompressed>true</zipCompressed>"
            f"</DownloadAttachment>"
        )
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{_NS_SOAP}"><s:Body>{body}</s:Body></s:Envelope>'
        )
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{_ACTION_DOWNLOAD_ATTACHMENT}"',
            "Accept-Encoding": "gzip, deflate",
        }
        url = f"{self.base_url}/CoreService.svc"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, content=envelope.encode("utf-8"), headers=headers)
        except httpx.HTTPError as exc:
            raise LisClientError(f"Transportfehler bei Dokument-Download: {exc}") from exc

        if self._on_exchange:
            try:
                self._on_exchange(
                    url, _ACTION_DOWNLOAD_ATTACHMENT, envelope.encode("utf-8"), resp.content,
                )
            except Exception:
                logger.exception("on_exchange-Hook fehlgeschlagen (wird ignoriert)")

        content_type = resp.headers.get("content-type", "")
        if "multipart/related" not in content_type:
            # Kein MTOM — z.B. SOAP Fault als normales XML
            root = ET.fromstring(resp.content)
            fault = _find_fault(root)
            raise LisClientError(fault or "Erwartete MTOM-Antwort, aber kein multipart/related erhalten")

        return _parse_mtom_binary(content_type, resp.content)


def _parse_mtom_binary(content_type: str, body: bytes) -> bytes:
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
    msg = email.message_from_bytes(header + body)
    binary_part = None
    for part in msg.walk():
        if part.get_content_type() == "application/octet-stream":
            binary_part = part
    if binary_part is None:
        raise LisClientError("MTOM-Antwort enthält keinen application/octet-stream-Teil")
    raw = binary_part.get_payload(decode=True)
    if raw is None:
        raise LisClientError("MTOM-Binärteil konnte nicht dekodiert werden")
    if raw[:2] == b"\x1f\x8b":  # zusätzlich gzip-komprimiert (Doku 4.5)
        raw = gzip.decompress(raw)
    return raw


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

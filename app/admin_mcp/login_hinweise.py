"""Feste, geheimnisfreie Login-Gruende fuer Formular und Audit-Anzeige."""

HINWEISE = {
    "global_aus": "Der Admin-MCP ist in den Systemeinstellungen noch nicht aktiviert.",
    "kein_zugriff": (
        "Fuer dieses Konto ist der Admin-MCP-Zugriff nicht freigegeben. Bitte in den "
        "Systemeinstellungen freigeben lassen."
    ),
    "kein_systemadministrator": (
        "Dieses Konto ist kein persoenlicher Systemadministrator. Bitte ein persoenliches "
        "Systemadministratorkonto verwenden."
    ),
    "nicht_eingerichtet": (
        "Bitte zuerst unter /mfa/einrichten die Zwei-Faktor-Authentifizierung einrichten und "
        "bestaetigen."
    ),
    "code_leer": "Bitte den Code aus der Authenticator-App oder einen Recovery-Code eingeben.",
    "ungueltig": (
        "Der Code ist ungueltig. Bitte den aktuellen Code und die Uhrzeit der Authenticator-App "
        "pruefen oder einen Recovery-Code verwenden."
    ),
    "verbraucht": (
        "Der Code wurde bereits verwendet. Bitte den naechsten Code abwarten (bis zu 30 Sekunden) "
        "oder einen Recovery-Code verwenden."
    ),
    "gesperrt": (
        "Die Zwei-Faktor-Authentifizierung ist voruebergehend gesperrt. Bitte in ca. {minuten} "
        "Minuten erneut versuchen."
    ),
    "authentifizierung_fehlgeschlagen": (
        "Passwort-Anmeldung fehlgeschlagen (Zugangsdaten, Kontosperre oder SSO-only pruefen)."
    ),
    "vorgang_abgelaufen": "Anmeldevorgang abgelaufen oder nicht mehr verfuegbar. Verbindung neu starten.",
}

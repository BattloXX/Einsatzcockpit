"""LIS/IPR-Anbindung (Intergraph Leitstelleninformationssystem).

Module:
  lis_client   – SOAP-Client (Login, Operations, Tasks, Units, Documents/MTOM)
  lis_mapping  – Parsing/Mapping-Helfer (Personen-Zu-Absage, Statuscodes, Stichwörter)
  lis_matching – Verknüpfungs-Logik LIS-Operation ↔ Incident
  lis_sync     – Sync-Orchestrierung je Organisation
  lis_loop     – Hintergrund-Poll-Loop (asyncio, siehe app/main.py lifespan)
"""

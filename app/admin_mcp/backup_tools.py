"""Lesende Backup-Betriebsdiagnose ohne Fachdaten."""
from app.admin_mcp.masking import mask_text
from app.admin_mcp.zeit import utc_response
from app.services.system_diagnose import backup_status


@utc_response
def collect_backup() -> dict:
    result = backup_status()
    evidence = result["evidence"]
    evidence["empfehlungen"] = [mask_text(text) for text in result["empfehlungen"]]
    return {"status": result["status"], "checked_at": result["checked_at"], "evidence": evidence}

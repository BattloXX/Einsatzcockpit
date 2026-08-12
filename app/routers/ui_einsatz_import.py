"""Admin-Oberfläche für den historischen Einsatz-Excel-Import."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.permissions import require_role
from app.core.templating import templates
from app.db import get_db
from app.models.user import User
from app.services.einsatz_import_service import EinsatzImportError, import_einsaetze, parse_einsatz_excel

router = APIRouter()


@router.get("/admin/einsatz-import", response_class=HTMLResponse)
def einsatz_import_page(
    request: Request,
    user: User = Depends(require_role("admin")),
):
    return templates.TemplateResponse(request, "admin/einsatz_import.html", {
        "request": request,
        "user": user,
        "org_missing": user.org_id is None,
    })


@router.post("/admin/einsatz-import")
async def einsatz_import_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    if user.org_id is None:
        return RedirectResponse("/admin/einsatz-import?error=org_missing", status_code=303)
    try:
        parsed = parse_einsatz_excel(await file.read())
        result = import_einsaetze(db, parsed, user.org_id, user.id)
    except EinsatzImportError as exc:
        return RedirectResponse(
            "/admin/einsatz-import?" + urlencode({"error": "invalid_excel", "error_detail": str(exc)}),
            status_code=303,
        )

    query = urlencode({
        "imported": result.imported,
        "updated": result.updated,
        "unchanged": result.unchanged,
        "vehicles_attached": result.vehicles_attached,
        "vehicles_adhoc": result.vehicles_adhoc,
        "row_errors": result.row_errors,
        "format": result.format,
        "warnings": len(result.warnings),
        "warning_detail": " | ".join(result.warnings[:20]),
        "error_detail": " | ".join(result.errors[:20]),
    })
    return RedirectResponse(f"/admin/einsatz-import?{query}", status_code=303)

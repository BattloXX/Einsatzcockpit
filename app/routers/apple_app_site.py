"""Oeffentliche Apple-Metadaten fuer Universal Links und Webcredentials."""

import re

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings

router = APIRouter()


@router.get("/.well-known/apple-app-site-association")
def apple_app_site_association() -> JSONResponse:
    headers = {"Cache-Control": "public, max-age=300"}
    if re.fullmatch(r"[A-Z0-9]{10}", settings.APPLE_TEAM_ID) is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404, headers=headers)

    app_id = f"{settings.APPLE_TEAM_ID}.cloud.einsatzleiter.app"
    return JSONResponse(
        {
            "applinks": {
                "details": [{
                    "appIDs": [app_id],
                    "components": [
                        {"/": "/api/*", "exclude": True},
                        {"/": "/static/*", "exclude": True},
                        {"/": "/*"},
                    ],
                }],
            },
            "webcredentials": {"apps": [app_id]},
        },
        headers=headers,
    )

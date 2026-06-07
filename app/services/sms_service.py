"""SMS-Versand über den SMS-Gateway-Docker-Container.

Wird später von Verifizierung, 2-Faktor-Auth und Info-SMS genutzt.
"""
import logging
import uuid

logger = logging.getLogger("einsatzleiter.sms")


async def send_sms(org_id: int, to: str, text: str, timeout: float = 15.0) -> bool:
    """Sendet eine SMS über den für org_id verbundenen SMS-Gateway-Container.

    Rückgabe: True bei Erfolg, False bei Fehler oder nicht verbundenem Gateway.
    """
    from app.routers.ws import dispatch_sms

    job_id = str(uuid.uuid4())
    try:
        result = await dispatch_sms(org_id, job_id, to, text, timeout=timeout)
        ok = result.get("ok", False)
        if not ok:
            logger.warning("SMS an %s fehlgeschlagen: %s", _mask(to), result.get("error"))
        return ok
    except RuntimeError as exc:
        logger.error("SMS-Versand fehlgeschlagen: %s", exc)
        return False


def _mask(number: str) -> str:
    if len(number) >= 5:
        return number[:-4] + "****"
    return "****"

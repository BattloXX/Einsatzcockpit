"""Kompatibilitaetsimport fuer bestehende TOTP-Aufrufer und Monkeypatches."""
import sys

from app.services import mfa_service
from app.services.mfa_service import *  # noqa: F403 -- bisherige oeffentliche Namen erhalten
from app.services.mfa_service import (
    LOCK_SECONDS,
    MAX_FAILED,
    MFA_POLICIES,
    admins,
    audit,
    clear_policy_cache,
    create_recovery_codes,
    diagnose_failure,
    enabled,
    fresh,
    generate_secret,
    matching_step,
    notify_reset,
    provisioning_uri,
    qr_data_uri,
    recovery_hash,
    required_for,
    reset,
    set_enabled,
    start_setup,
    totp,
    verify,
)

__all__ = [
    "LOCK_SECONDS",
    "MAX_FAILED",
    "MFA_POLICIES",
    "admins",
    "audit",
    "clear_policy_cache",
    "create_recovery_codes",
    "diagnose_failure",
    "enabled",
    "fresh",
    "generate_secret",
    "matching_step",
    "notify_reset",
    "provisioning_uri",
    "qr_data_uri",
    "recovery_hash",
    "required_for",
    "reset",
    "set_enabled",
    "start_setup",
    "totp",
    "verify",
]

# Ein Modulobjekt fuer beide Importpfade: Patches wirken auch auf interne Aufrufe.
sys.modules[__name__] = mfa_service

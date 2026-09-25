"""Firebase credential loading with explicit, non-repository configuration."""

import json
import os

from firebase_admin import credentials


def load_firebase_credential():
    """Return configured Firebase credentials, or None for managed ADC.

    Local development may provide the service-account JSON through the
    FIREBASE_SERVICE_ACCOUNT environment variable. Production may provide an
    explicitly managed GOOGLE_APPLICATION_CREDENTIALS path. This helper never
    searches the repository for credential files.
    """
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if service_account_json:
        try:
            service_account_info = json.loads(service_account_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT must contain valid JSON") from exc
        if not isinstance(service_account_info, dict):
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT must contain a JSON object")
        return credentials.Certificate(service_account_info)

    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if cred_path:
        if not os.path.isfile(cred_path):
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS points to a missing file")
        return credentials.Certificate(cred_path)

    return None

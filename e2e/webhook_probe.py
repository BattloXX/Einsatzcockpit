"""Send signed synthetic Resend/Svix webhook events to a running instance."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("secret")
    parser.add_argument("event_type", choices=("email.delivered", "email.bounced"))
    parser.add_argument("message_id")
    parser.add_argument("email")
    parser.add_argument("--svix-id", required=True)
    args = parser.parse_args()

    payload = {
        "type": args.event_type,
        "data": {
            "email_id": args.message_id,
            "to": [args.email],
            "bounce": {"type": "hard"},
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    encoded_secret = args.secret.removeprefix("whsec_")
    key = base64.b64decode(encoded_secret, validate=True)
    signed = f"{args.svix_id}.{timestamp}.{body.decode()}".encode()
    signature = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    request = urllib.request.Request(
        args.url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "svix-id": args.svix_id,
            "svix-timestamp": timestamp,
            "svix-signature": "v1," + signature,
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.status, response.read().decode())


if __name__ == "__main__":
    main()

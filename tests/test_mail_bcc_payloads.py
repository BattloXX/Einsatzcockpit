"""Bcc/Cc-Empfänger in den Provider-Payloads."""
from email import policy
from email.headerregistry import HeaderRegistry, UnstructuredHeader
from email.message import EmailMessage

from app.services.o365_mail_service import _graph_payload
from app.services.resend_mail_service import _resend_payload


def _message() -> EmailMessage:
    header_factory = HeaderRegistry()
    for header in ("to", "cc", "bcc"):
        header_factory.map_to_type(header, UnstructuredHeader)
    msg = EmailMessage(policy=policy.default.clone(header_factory=header_factory))
    msg["To"] = "sender@example.at"
    msg["Subject"] = "Test"
    msg.set_content("Inhalt")
    return msg


def test_resend_payload_includes_cc_and_bcc_addresses_split_by_comma_and_semicolon():
    msg = _message()
    msg["Cc"] = "cc-one@example.at; cc-two@example.at"
    msg["Bcc"] = "bcc-one@example.at, bcc-two@example.at"

    payload = _resend_payload(msg, "sender@example.at")

    assert payload["cc"] == ["cc-one@example.at", "cc-two@example.at"]
    assert payload["bcc"] == ["bcc-one@example.at", "bcc-two@example.at"]


def test_resend_payload_omits_bcc_when_header_is_missing():
    assert "bcc" not in _resend_payload(_message(), "sender@example.at")


def test_graph_payload_includes_bcc_addresses_and_omits_them_when_missing():
    msg = _message()
    msg["Bcc"] = "bcc-one@example.at; bcc-two@example.at"

    payload = _graph_payload(msg)

    assert payload["message"]["bccRecipients"] == [
        {"emailAddress": {"address": "bcc-one@example.at"}},
        {"emailAddress": {"address": "bcc-two@example.at"}},
    ]
    assert "bccRecipients" not in _graph_payload(_message())["message"]

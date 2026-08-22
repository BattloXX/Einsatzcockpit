import pytest
from jinja2 import UndefinedError

from app.services.mailing_render import render_mailing


def test_renders_whitelisted_values():
    assert render_mailing("Hallo {{ vorname }}", {"vorname": "Anna"}) == "Hallo Anna"


def test_unknown_value_is_strict():
    with pytest.raises(UndefinedError):
        render_mailing("{{ request }}", {"request": "secret"})


def test_sandbox_blocks_object_access():
    with pytest.raises(Exception):
        render_mailing("{{ x.__class__ }}", {"x": "ignored"})

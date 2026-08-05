from pathlib import Path

TEMPLATES = [
    Path("app/templates/login.html"),
    *sorted(Path("app/templates/auth").glob("*.html")),
    Path("app/templates/errors/fehler.html"),
    Path("app/templates/public/base.html"),
]


def test_public_entry_templates_have_versioned_manifest_links():
    assert len(list(Path("app/templates/auth").glob("*.html"))) == 8
    for template in TEMPLATES:
        source = template.read_text(encoding="utf-8")
        assert 'rel="manifest"' in source, template
        assert "?v={{ IMG_VERSION }}" in source, template

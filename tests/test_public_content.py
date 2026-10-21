from pathlib import Path

from app.services.public_content import FEATURES

WIKI_DIR = Path(__file__).resolve().parents[1] / "docs" / "wiki"


def _wiki_slugs() -> set[str]:
    slugs: set[str] = set()
    for feature in FEATURES:
        entries = [feature, *feature.get("bullets", [])]
        for group in feature.get("groups", []):
            entries.extend([group, *group.get("bullets", [])])
        for entry in entries:
            if isinstance(entry, dict) and entry.get("wiki"):
                slugs.add(entry["wiki"].split("#", 1)[0])
    return slugs


def test_wiki_links_refer_to_existing_pages():
    missing = [slug for slug in _wiki_slugs() if not (WIKI_DIR / f"{slug}.md").is_file()]
    assert not missing, f"Fehlende Wiki-Seiten: {', '.join(sorted(missing))}"


def test_public_pages_and_feature_anchors(client):
    for path in ("/", "/funktionen", "/roadmap"):
        response = client.get(path)
        assert response.status_code == 200

    functions = client.get("/funktionen")
    for anchor in ("einsatzinfo", "einsatzboard", "grossschadenslage", "verwaltung", "schnittstellen"):
        assert f'id="{anchor}"' in functions.text


def test_public_content_icons_are_vendored():
    from app.core.icons import icon_namen
    from app.services.public_content import PRINCIPLES

    assert {item["icon"] for item in [*FEATURES, *PRINCIPLES]} <= icon_namen()


def test_cms_default_icons_render_without_evaluating_saved_jinja():
    from app.core.templating import templates
    from app.services.site_pages import DEFAULT_HTML

    template = templates.get_template("public/page.html")
    html = template.render(body_html=DEFAULT_HTML["landing"], preview=False, contact=False)
    assert html.count('<svg class="ecp-icon ecp-icon--lg"') == 6
    assert 'data-ecp-icon=' not in html
    saved = template.render(body_html='{{ 7 * 7 }} 🫁', preview=False, contact=False)
    assert '{{ 7 * 7 }} 🫁' in saved

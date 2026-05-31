"""Öffentliche Seiten als frei editierbare HTML-Blöcke (WYSIWYG-CMS).

Jede Seite (``landing``, ``impressum``, ``about``) wird als kompletter HTML-Body
in ``system_settings`` unter dem Schlüssel ``page.<slug>.html`` gespeichert und von
Systemadmins über einen WYSIWYG-Editor gepflegt. Header, Footer und – auf der
Startseite – das funktionale Kontaktformular bleiben als festes Gerüst erhalten.

Sicherheitshinweis: Der gespeicherte HTML-Code wird ungefiltert ausgegeben
(``| safe``). Nur ``system_admin`` darf ihn bearbeiten – das ist die höchste
Vertrauensstufe (Zugriff auf Backup, Benutzer, Systemeinstellungen). Das CMS ist
also bewusst „roher HTML"-Editor wie in vergleichbaren Admin-Tools.
"""
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.models.master import SystemSettings

logger = logging.getLogger("einsatzleiter.site_pages")

KEY_PREFIX = "page."
UPLOAD_DIR = Path("app_storage/site_pages")

# Slug → (Anzeigename, Kontaktformular anzeigen?)
PAGES: dict[str, dict] = {
    "landing": {"title": "Startseite", "contact": True, "label": "Startseite"},
    "impressum": {"title": "Impressum", "contact": False, "label": "Impressum"},
    "about": {"title": "Über", "contact": False, "label": "Über uns"},
    "datenschutz": {"title": "Datenschutz", "contact": False, "label": "Datenschutz"},
}

ALLOWED_IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

DEFAULT_HTML: dict[str, str] = {
    "landing": """
<section class="lp-hero">
  <div class="lp-wrap">
    <h1>Digitale Einsatzleitung <span class="hl">in Echtzeit.</span></h1>
    <p class="lead">Das digitale Hilfswerkzeug für Feuerwehren und Rettungskräfte.
    Behalten Sie den Überblick, wenn jede Sekunde zählt – mobil, sicher und für den
    Ernstfall gemacht.</p>
    <p>
      <a href="#features" class="lp-btn lp-btn--primary">Funktionsumfang ansehen</a>
      <a href="#kontakt" class="lp-btn lp-btn--ghost">Kontakt aufnehmen</a>
    </p>
  </div>
</section>

<section class="lp-section" id="features">
  <div class="lp-wrap">
    <h2>Gemacht für den Ernstfall</h2>
    <p class="lp-section__sub">Alles, was die Einsatzleitung im Ernstfall braucht – in einem Werkzeug.</p>
    <div class="lp-features">
      <div class="lp-feature"><div class="lp-feature__icon">🚒</div>
        <h3>Echtzeit-Fahrzeugübersicht</h3>
        <p>FMS-Status und Mannschaftsstärke jedes Fahrzeugs auf einen Blick – live synchronisiert.</p></div>
      <div class="lp-feature"><div class="lp-feature__icon">📋</div>
        <h3>Auftrags-Management</h3>
        <p>Übersichtliche Kanban-Boards: Aufträge zuweisen, priorisieren und den Fortschritt verfolgen.</p></div>
      <div class="lp-feature"><div class="lp-feature__icon">📨</div>
        <h3>Echtzeit-Meldungen</h3>
        <p>Lagemeldungen und Statusupdates sekundenschnell direkt vom Einsatzort.</p></div>
      <div class="lp-feature"><div class="lp-feature__icon">🫁</div>
        <h3>Atemschutz-Überwachung</h3>
        <p>Trupp- und Drucküberwachung mit lückenloser Erfassung der Atemschutzträger.</p></div>
      <div class="lp-feature"><div class="lp-feature__icon">👥</div>
        <h3>Personen &amp; Patienten</h3>
        <p>Betroffene erfassen, Triage-Status und Transportkapazitäten jederzeit im Blick.</p></div>
      <div class="lp-feature"><div class="lp-feature__icon">📄</div>
        <h3>Einsatzchronik &amp; PDF</h3>
        <p>Lückenlose, rechtssichere Protokollierung aller Schritte – auf Knopfdruck als PDF.</p></div>
    </div>
  </div>
</section>

<section class="lp-section lp-section--alt" id="about-section">
  <div class="lp-wrap">
    <h2 style="text-align:left">Über einsatzleiter.cloud</h2>
    <p>einsatzleiter.cloud ist ein digitales Hilfswerkzeug für die Einsatzleitung von Feuerwehren
    und Rettungsdiensten. Es entstand aus der Praxis: aus dem Bedürfnis, bei Einsätzen den Überblick
    zu behalten, Aufträge sauber zu koordinieren und alles rechtssicher zu dokumentieren – ohne
    Zettelwirtschaft.</p>
    <p><a href="/about" class="lp-btn lp-btn--ghost">Mehr erfahren</a></p>
  </div>
</section>
""",
    "impressum": """
<div class="lp-doc">
  <h1>Impressum</h1>

  <h2>Betreiber</h2>
  <p>Johannes Battlogg</p>

  <h2>Kontakt</h2>
  <p>E-Mail: <a href="mailto:johannes@battlogg.org">johannes@battlogg.org</a></p>

  <h2>Projekt</h2>
  <p>Idee: Roman Reiter<br>Umsetzung: Johannes Battlogg</p>

  <h2>Haftungsausschluss</h2>
  <p>Die Inhalte dieser Seite wurden mit größtmöglicher Sorgfalt erstellt. Für die Richtigkeit,
  Vollständigkeit und Aktualität der Inhalte wird jedoch keine Gewähr übernommen.</p>
</div>
""",
    "about": """
<div class="lp-doc">
  <h1>Über einsatzleiter.cloud</h1>
  <p>einsatzleiter.cloud ist ein digitales Hilfswerkzeug für die Einsatzleitung von Feuerwehren und
  Rettungsdiensten. Es entstand aus der Praxis – aus dem Bedürfnis, bei Einsätzen den Überblick zu
  behalten, Aufträge sauber zu koordinieren und alles rechtssicher zu dokumentieren.</p>
  <p>Das Werkzeug ist bewusst mobil gedacht, läuft im Einsatzleitwagen genauso wie am Tablet vor Ort
  und synchronisiert alle Informationen in Echtzeit zwischen den Beteiligten.</p>

  <h2>Idee &amp; Umsetzung</h2>
  <p>Idee: Roman Reiter<br>Umsetzung: Johannes Battlogg</p>

  <p style="margin-top:28px">
    <a href="/#kontakt" class="lp-btn lp-btn--primary">Kontakt aufnehmen</a>
    <a href="/" class="lp-btn lp-btn--ghost">Zur Startseite</a>
  </p>
</div>
""",
    "datenschutz": """
<div class="lp-doc">
  <h1>Datenschutzerklärung</h1>
  <p>Der Schutz Ihrer personenbezogenen Daten ist uns wichtig. Nachfolgend informieren wir Sie
  über die Verarbeitung personenbezogener Daten bei der Nutzung von einsatzleiter.cloud.</p>

  <h2>Verantwortlicher</h2>
  <p>Johannes Battlogg<br>E-Mail: <a href="mailto:johannes@battlogg.org">johannes@battlogg.org</a></p>

  <h2>Verarbeitete Daten</h2>
  <p>Bei der Nutzung der Anwendung werden die zur Einsatzdokumentation eingegebenen Daten sowie
  technisch notwendige Zugangs- und Sitzungsdaten (z.&nbsp;B. Anmeldedaten, Session-Cookie)
  verarbeitet. Server-Logfiles können IP-Adresse, Zeitpunkt und aufgerufene Ressource enthalten.</p>

  <h2>Kontaktformular</h2>
  <p>Wenn Sie uns über das Kontaktformular schreiben, werden die von Ihnen angegebenen Daten
  (Name, E-Mail-Adresse, Nachricht) zur Bearbeitung der Anfrage per E-Mail an uns übermittelt
  und gespeichert.</p>

  <h2>Cookies</h2>
  <p>Wir verwenden ausschließlich technisch notwendige Cookies (Anmeldung/Session sowie ein
  Cookie zum Schutz vor Cross-Site-Request-Forgery). Es findet kein Tracking und keine Analyse
  durch Dritte statt.</p>

  <h2>Rechtsgrundlage</h2>
  <p>Die Verarbeitung erfolgt zur Erfüllung vertraglicher bzw. vorvertraglicher Maßnahmen
  (Art.&nbsp;6 Abs.&nbsp;1 lit.&nbsp;b DSGVO) sowie auf Grundlage unseres berechtigten Interesses
  am sicheren Betrieb der Anwendung (Art.&nbsp;6 Abs.&nbsp;1 lit.&nbsp;f DSGVO).</p>

  <h2>Ihre Rechte</h2>
  <p>Sie haben das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung,
  Datenübertragbarkeit sowie Widerspruch. Wenden Sie sich dafür an die oben genannte
  Kontaktadresse. Zudem besteht ein Beschwerderecht bei der zuständigen Aufsichtsbehörde.</p>
</div>
""",
}


def get_page_html(db, slug: str) -> str:
    """Gespeichertes HTML der Seite oder den Default-Inhalt."""
    row = db.query(SystemSettings).filter_by(key=KEY_PREFIX + slug + ".html").first()
    if row and row.value:
        return row.value
    return DEFAULT_HTML.get(slug, "")


def set_page_html(db, slug: str, html: str, user_id: int | None = None) -> None:
    key = KEY_PREFIX + slug + ".html"
    row = db.query(SystemSettings).filter_by(key=key).first()
    if row is None:
        row = SystemSettings(key=key)
        db.add(row)
    row.value = html
    row.updated_at = datetime.now(UTC)
    row.updated_by_user_id = user_id


def list_images() -> list[dict]:
    """Hochgeladene Bilder (neueste zuerst) für die Editor-Galerie."""
    if not UPLOAD_DIR.is_dir():
        return []
    files = [
        p for p in UPLOAD_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_IMG_EXTS
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "url": f"/seite/bild/{p.name}"} for p in files]

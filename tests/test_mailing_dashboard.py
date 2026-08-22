from pathlib import Path
def test_dashboard_template_contains_all_kpis_and_charts():
    text=Path("app/templates/mailing/dashboard.html").read_text(); assert all(x in text for x in ("Kampagnen","Versendet","Open-Rate","Click-Rate","Rückstand","Fehler (24 h)","campaignRates","sendsOverTime","failureBreakdown","mailing_dashboard.js"))

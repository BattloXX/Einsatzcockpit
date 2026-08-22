from app.services.mailing_service import build_mailing_dashboard_data
from app.core.templating import templates
from tests.mailing_phase2_helpers import db_session

def test_report_data_and_print_template_render():
    db=db_session(); data=build_mailing_dashboard_data(db)
    html=templates.env.get_template("mailing/dashboard_report.html").render(**data,org=None,pdf=False)
    assert "Mailing-Bericht" in html and "@media print" in html and "total_campaigns" in data
    db.close()

from app.services.mailing_service import import_csv


def test_csv_import_callable():
    assert callable(import_csv)

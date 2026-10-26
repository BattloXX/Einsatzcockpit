import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine


def test_app_engine_redacts_bound_values_in_sql_errors():
    assert engine.hide_parameters
    secret = "PRIVATE_SMS_TEXT_AND_TOKEN_VALUE"
    with engine.connect() as connection, pytest.raises(SQLAlchemyError) as error:
        connection.exec_driver_sql("SELECT ? FROM phase6_nonexistent_table_for_redaction", (secret,))
    assert secret not in str(error.value)
    assert "parameters hidden" in str(error.value)

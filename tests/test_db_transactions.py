from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError

from app.services.db_transactions import commit_db_operation, current_read_session, retry_claim


def conflict(code):
    return OperationalError("INSERT", {}, Exception(code, "private details"))


@pytest.mark.parametrize("code", [1020, 1213])
def test_retry_rolls_back_whole_operation_before_commit(code):
    db = Mock()
    operation = Mock(side_effect=[conflict(code), "result"])
    assert commit_db_operation(db, operation, label="test") == "result"
    assert operation.call_count == 2
    db.rollback.assert_called_once()
    db.commit.assert_called_once()


def test_commit_conflict_repeats_operation_but_success_returns_once():
    db = Mock()
    db.commit.side_effect = [conflict(1213), None]
    operation = Mock(return_value="result")
    after_commit = Mock()
    result = commit_db_operation(db, operation, label="test")
    after_commit(result)
    assert operation.call_count == 2
    after_commit.assert_called_once_with("result")


@pytest.mark.parametrize("error", [conflict(2006), ValueError("invalid")])
def test_unknown_error_is_never_retried(error):
    db = Mock()
    operation = Mock(side_effect=error)
    with pytest.raises(type(error)):
        commit_db_operation(db, operation, label="test")
    operation.assert_called_once()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_conflicts_are_bounded():
    db = Mock()
    operation = Mock(side_effect=conflict(1020))
    with pytest.raises(OperationalError):
        commit_db_operation(db, operation, label="test")
    assert operation.call_count == 3 and db.rollback.call_count == 3


@pytest.mark.parametrize("dialect", ["mysql", "mariadb", "sqlite"])
def test_queue_isolation_is_applied_only_to_new_mysql_sessions(dialect):
    db = Mock()
    db.get_bind.return_value.dialect.name = dialect
    assert current_read_session(lambda: db) is db
    db.connection.assert_not_called()
    if dialect == "sqlite":
        db.get_bind.return_value.execution_options.assert_not_called()
    else:
        db.get_bind.return_value.execution_options.assert_called_once_with(isolation_level="READ COMMITTED")
        assert db.bind == db.get_bind.return_value.execution_options.return_value


def test_session_is_closed_when_initialization_fails():
    db = Mock()
    db.get_bind.side_effect = ValueError("invalid factory")
    with pytest.raises(ValueError):
        current_read_session(lambda: db)
    db.close.assert_called_once()


def test_claim_conflict_restarts_transaction_and_returns_once(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("app.services.db_transactions.time.sleep", sleep)
    db = Mock()
    db.get_bind.return_value.dialect.name = "mysql"
    operation = Mock(side_effect=[conflict(1020), "claimed"])
    assert retry_claim(db, operation) == "claimed"
    assert operation.call_count == 2
    db.rollback.assert_called_once()
    db.commit.assert_not_called()  # Commit bleibt bei der Claim-Operation.
    assert 0.01 <= sleep.call_args.args[0] <= 0.019


def test_claim_conflicts_are_bounded_and_preserve_exception(monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("app.services.db_transactions.time.sleep", sleep)
    db = Mock()
    db.get_bind.return_value.dialect.name = "mysql"
    error = conflict(1020)
    operation = Mock(side_effect=error)
    with pytest.raises(OperationalError) as caught:
        retry_claim(db, operation)
    assert caught.value is error
    assert operation.call_count == db.rollback.call_count == 3
    assert sleep.call_count == 2


@pytest.mark.parametrize("dialect,code", [("sqlite", 1020), ("mysql", 1205), ("mysql", 1213), ("mysql", 2006)])
def test_claim_does_not_retry_sqlite_or_other_errors(dialect, code):
    db = Mock()
    db.get_bind.return_value.dialect.name = dialect
    operation = Mock(side_effect=conflict(code))
    with pytest.raises(OperationalError):
        retry_claim(db, operation)
    operation.assert_called_once()
    db.rollback.assert_not_called()

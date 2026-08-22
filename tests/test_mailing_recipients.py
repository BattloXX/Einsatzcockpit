from app.services.mailing_recipients import (
    resolve_all_members,
    resolve_incident_commanders,
    resolve_incident_participants,
)


def test_three_resolvers_exist():
    assert all(callable(x) for x in (resolve_all_members, resolve_incident_commanders, resolve_incident_participants))

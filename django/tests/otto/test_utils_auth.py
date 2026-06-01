from otto.utils.auth import map_entra_to_django_user


def test_map_entra_to_django_user_maps_supported_profile_fields():
    mapped = map_entra_to_django_user(
        mail="casey.employee@justice.gc.ca",
        givenName="Casey",
        surname="Employee",
        oid="oid-123",
        jobTitle="Analyst | Analyste",
        preferredLanguage="English",
    )

    assert mapped["email"] == "casey.employee@justice.gc.ca"
    assert mapped["job_title"] == "Analyst | Analyste"
    assert mapped["preferred_language"] == "English"
    assert "employee_type" not in mapped

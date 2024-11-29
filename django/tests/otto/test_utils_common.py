def test_file_size_to_string():
    from otto.utils.common import file_size_to_string

    assert file_size_to_string(10) == "10 bytes"
    assert file_size_to_string(1024) == "1.00 KB"
    assert file_size_to_string(1024 * 1024) == "1.00 MB"
    assert file_size_to_string(1024 * 1024 * 1024) == "1024.00 MB"


def test_get_app_from_path():
    from otto.utils.common import get_app_from_path

    assert get_app_from_path("http://localhost:8000/chat/somthing/") == "chat"
    assert get_app_from_path("http://localhost:8000/") == "Otto"
    assert get_app_from_path("http://localhost:8000") == "Otto"
    assert get_app_from_path("http://localhost:8000/laws/nested/path/") == "laws"
    assert get_app_from_path("") == "Otto"

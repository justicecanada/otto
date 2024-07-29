from otto.utils.common import file_size_to_string


def test_file_size_to_string():
    assert file_size_to_string(10) == "10 bytes"
    assert file_size_to_string(1024) == "1.00 KB"
    assert file_size_to_string(1024 * 1024) == "1.00 MB"
    assert file_size_to_string(1024 * 1024 * 1024) == "1024.00 MB"

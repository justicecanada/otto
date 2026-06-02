import os
import tempfile

from django.conf import settings

import pytest

from otto.utils.common import get_temp_dir


@pytest.mark.django_db
def test_get_temp_dir_in_media_root(tmp_path):
    """get_temp_dir should live under MEDIA_ROOT/tmp and be created if missing."""
    temp_dir = get_temp_dir()

    assert temp_dir.startswith(settings.MEDIA_ROOT)
    assert temp_dir.endswith("tmp")
    assert os.path.exists(temp_dir)
    assert os.path.isdir(temp_dir)


@pytest.mark.django_db
def test_namedtemporaryfile_uses_shared_tmp_dir():
    """Creating a NamedTemporaryFile in get_temp_dir should produce a path under MEDIA_ROOT/tmp."""
    temp_dir = get_temp_dir()
    with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as f:
        f.write(b"hello")
        path = f.name

    try:
        assert path.startswith(temp_dir)
        assert os.path.exists(path)
        with open(path, "rb") as rf:
            assert rf.read() == b"hello"
    finally:
        # cleanup
        if os.path.exists(path):
            os.unlink(path)

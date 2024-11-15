import pytest
from otto.models import OttoStatus
from otto.tasks import update_exchange_rate

@pytest.mark.django_db()
def test_exchange_rate_update(client, all_apps_user):
    update_exchange_rate()
    otto_status = OttoStatus.objects.singleton()
    saved_exchange_rate = otto_status.exchange_rate
    decimal_places = str(saved_exchange_rate).split('.')[-1] 
    assert isinstance(saved_exchange_rate, float)
    assert len(decimal_places) < 5 and len(decimal_places) > 1

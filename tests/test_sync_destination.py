"""La identidad excluye credenciales, pero distingue destinos comerciales."""

import pytest

from app.schemas.sync_destination import SyncDestination


STORE = "a3a56a06-d40c-4c07-931c-41abcbaaba12"


def destination(url, store=STORE):
    return SyncDestination.from_url(url, environment="cloud", store_id=store)


def test_same_neon_endpoint_allows_reader_pooler_and_operator_direct():
    reader = destination("postgresql://reader:reader-password@ep-one-pooler.region.aws.neon.tech/catalog")
    operator = destination("postgresql://operator:operator-password@ep-one.region.aws.neon.tech:5432/catalog", STORE.upper())
    assert reader == operator
    assert reader.port == 5432
    assert "password" not in reader.model_dump_json()


@pytest.mark.parametrize("url", [
    "postgresql://operator@ep-two.region.aws.neon.tech/catalog",
    "postgresql://operator@ep-one.other.aws.neon.tech/catalog",
    "postgresql://operator@ep-one.region.aws.neon.tech/other",
    "postgresql://operator@ep-one.region.aws.neon.tech:5433/catalog",
    "postgresql://operator@ep-one-pooler.example.com/catalog",
])
def test_similar_hosts_and_databases_are_not_equivalent(url):
    assert destination(url) != destination("postgresql://reader@ep-one-pooler.region.aws.neon.tech/catalog")

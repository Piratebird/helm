import pytest
import responses

from helm.core.indexer_manager import ProwlarrManager


@pytest.fixture(autouse=True)
def _prowlarr_env(monkeypatch):
    monkeypatch.setenv("PROWLARR_URL", "http://localhost:19696")
    monkeypatch.setenv("PROWLARR_API_KEY", "testapikey")


@pytest.fixture
def schema_payload():
    return [
        {"definitionName": "1337x", "name": "1337x", "implementation": "Cardigann", "privacy": "public"},
        {"definitionName": "YTS", "name": "YTS (Public)", "implementation": "YtsApi", "privacy": "public"},
    ]


@responses.activate
def test_prowlarr_manager_get_indexers(schema_payload):
    responses.add(responses.GET, "http://localhost:19696/api/v1/indexer/schema", json=schema_payload, status=200)
    responses.add(
        responses.GET,
        "http://localhost:19696/api/v1/indexer",
        json=[
            {"id": 1, "enable": True, "definitionName": "1337x", "implementation": "Cardigann"},
            {"id": 2, "enable": False, "definitionName": "YTS", "implementation": "YtsApi"},
        ],
        status=200,
    )

    manager = ProwlarrManager()
    indexers = manager.get_all_indexers()

    assert len(indexers) == 2
    assert indexers[0]["id"] == "1"
    assert indexers[0]["configured"] is True
    assert indexers[0]["title"] == "1337x"
    assert indexers[1]["id"] == "2"
    assert indexers[1]["configured"] is False


@responses.activate
def test_prowlarr_manager_add_remove():
    responses.add(
        responses.GET,
        "http://localhost:19696/api/v1/indexer/1",
        json={"id": 1, "enable": False, "name": "1337x"},
        status=200,
    )
    responses.add(
        responses.PUT,
        "http://localhost:19696/api/v1/indexer/1",
        json={"id": 1, "enable": True, "name": "1337x"},
        status=200,
    )

    manager = ProwlarrManager()
    assert manager.add_indexer("1") is True

    responses.add(
        responses.GET,
        "http://localhost:19696/api/v1/indexer/2",
        json={"id": 2, "enable": True, "name": "YTS"},
        status=200,
    )
    responses.add(
        responses.PUT,
        "http://localhost:19696/api/v1/indexer/2",
        json={"id": 2, "enable": False, "name": "YTS"},
        status=200,
    )
    assert manager.remove_indexer("2") is True


@responses.activate
def test_prowlarr_seed_default_indexers(schema_payload):
    responses.add(responses.GET, "http://localhost:19696/api/v1/indexer/schema", json=schema_payload, status=200)
    responses.add(responses.GET, "http://localhost:19696/api/v1/indexer", json=[], status=200)
    responses.add(responses.POST, "http://localhost:19696/api/v1/indexer", json={"id": 9, "enable": True}, status=200)

    manager = ProwlarrManager()
    enabled = manager.seed_default_indexers()

    assert enabled == 2

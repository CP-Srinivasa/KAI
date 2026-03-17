def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_sources_placeholder(client):
    response = client.get("/sources")
    assert response.status_code == 200


def test_query_validate_empty(client):
    response = client.post("/query/validate", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True


def test_query_validate_with_terms(client):
    payload = {"include_terms": ["bitcoin", "ethereum"], "limit": 10}
    response = client.post("/query/validate", json=payload)
    assert response.status_code == 200
    assert response.json()["valid"] is True

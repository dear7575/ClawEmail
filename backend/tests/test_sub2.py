def test_sub2_conversion_route_is_removed(test_client) -> None:
    response = test_client.post("/api/sub2/convert", json={"input": {"accessToken": "token"}})

    assert response.status_code == 404


def test_sub2_account_push_routes_are_removed(test_client) -> None:
    payload = {"input": {"accessToken": "token"}}
    assert test_client.post("/api/sub2/push", json=payload).status_code == 404
    assert test_client.post("/api/sub2/push-data/jobs", json={"data": {"accounts": [{}]}}).status_code == 404


def test_obsolete_sub2_configuration_routes_are_removed(test_client) -> None:
    assert test_client.get("/api/sub2/settings").status_code == 404
    assert test_client.put("/api/sub2/settings", json={}).status_code == 404
    assert test_client.get("/api/sub2/groups").status_code == 404
    assert test_client.get("/api/sub2/proxies").status_code == 404


def test_openai_duck_push_route_is_removed(test_client) -> None:
    response = test_client.post("/api/openai/duck-push-sub2", json={"duckAddressId": 1})

    assert response.status_code == 404

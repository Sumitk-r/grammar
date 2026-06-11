def test_create_job_and_reuse_active_duplicate(client):
    payload = {"url": "https://www.khanacademy.org/humanities/grammar"}
    first = client.post("/api/jobs", json=payload)
    second = client.post("/api/jobs", json=payload)

    assert first.status_code == 201
    assert first.json()["status"] == "queued"
    assert second.status_code == 201
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["reused"] is True


def test_rejects_non_khan_url(client):
    response = client.post(
        "/api/jobs",
        json={"url": "https://example.com/humanities/grammar"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Please enter a valid Khan Academy course URL."


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


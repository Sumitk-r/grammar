from app.config import settings


def create_category(client, name="Grammar"):
    response = client.post(
        "/api/categories",
        json={"name": name},
        headers={"X-Admin-Key": settings.admin_key},
    )
    assert response.status_code == 201
    return response.json()


def test_admin_can_create_category(client):
    category = create_category(client, "Science")

    assert category["name"] == "Science"
    assert category["slug"] == "science"

    response = client.get("/api/categories")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Science"


def test_non_admin_cannot_create_category(client):
    response = client.post("/api/categories", json={"name": "Science"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Admin key is required."

    response = client.post(
        "/api/categories",
        json={"name": "Science"},
        headers={"X-Admin-Key": "wrong"},
    )
    assert response.status_code == 401


def test_create_job_and_reuse_active_duplicate(client):
    category = create_category(client)
    payload = {
        "url": "https://www.khanacademy.org/humanities/grammar",
        "category_id": category["id"],
    }
    first = client.post("/api/jobs", json=payload)
    second = client.post("/api/jobs", json=payload)

    assert first.status_code == 201
    assert first.json()["status"] == "queued"
    assert second.status_code == 201
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["reused"] is True


def test_rejects_unsupported_source_url(client):
    category = create_category(client)
    response = client.post(
        "/api/jobs",
        json={
            "url": "https://example.com/humanities/grammar",
            "category_id": category["id"],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Please enter a valid Khan Academy course or YouTube playlist URL."


def test_rejects_job_with_unknown_category(client):
    response = client.post(
        "/api/jobs",
        json={
            "url": "https://www.khanacademy.org/humanities/grammar",
            "category_id": "missing",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Category not found."


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

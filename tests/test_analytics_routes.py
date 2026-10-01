"""Las respuestas de los estudiantes y el resumen para el docente."""

import uuid

from fastapi.testclient import TestClient

from assessment_service.main import app
from neon_storage import SessionLocal
from neon_storage.models import Usuario
from user_management_service.core.security import create_access_token

client = TestClient(app)


def _user(rol: str) -> tuple[int, dict]:
    email = f"{rol}-{uuid.uuid4().hex[:8]}@example.com"
    with SessionLocal() as db:
        user = Usuario(nombre=rol.title(), correo=email, password="x", rol=rol)
        db.add(user)
        db.commit()
        user_id = user.id_usuario

    token = create_access_token(data={"sub": str(user_id), "email": email, "rol": rol})
    return user_id, {"Authorization": f"Bearer {token}"}


def test_a_student_records_answers_and_the_teacher_sees_them():
    _, student = _user("estudiante")
    _, teacher = _user("docente")

    recorded = client.post(
        "/api/analytics/attempts",
        json={
            "section_id": "m2-s1",
            "activity": "quiz",
            "answers": [
                {"question_index": 0, "prompt": "¿2+2?", "correct": True, "elapsed_ms": 4000},
                {"question_index": 1, "prompt": "¿3*3?", "correct": False},
            ],
        },
        headers=student,
    )
    assert recorded.status_code == 201
    assert recorded.json() == {"recorded": 2}

    summary = client.get("/api/analytics/summary", headers=teacher).json()
    section = next(s for s in summary["sections"] if s["section_id"] == "m2-s1")
    assert section["answered"] == 2
    assert section["correct"] == 1


def test_completing_the_same_activity_twice_counts_once():
    _, student = _user("estudiante")
    body = {"section_id": "m2-s2", "activity": "lectura"}

    first = client.post("/api/analytics/completions", json=body, headers=student)
    second = client.post("/api/analytics/completions", json=body, headers=student)

    assert first.json()["recorded"] is True
    assert second.json()["recorded"] is False


def test_a_student_cannot_read_the_course_summary():
    _, student = _user("estudiante")

    assert client.get("/api/analytics/summary", headers=student).status_code == 403

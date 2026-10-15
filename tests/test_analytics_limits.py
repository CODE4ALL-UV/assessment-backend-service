"""Lo que el registro de respuestas rechaza, y cómo se borran los datos de prueba.

Un cliente roto o una sesión vieja no pueden llenar la base de basura ni
devolver un error del servidor: cada caso tiene su respuesta clara. Y borrar
lo registrado en un curso no puede tocar lo de otro.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from assessment_service.main import app
from assessment_service.presentation.api.analytics_routes import MAX_ANSWERS
from neon_storage import SessionLocal, get_db
from neon_storage.courses import new_join_code
from neon_storage.models import ActivityCompletion, Course, CourseEnrollment, QuizAnswer, Usuario
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


def _course(teacher_id: int, *students: int) -> int:
    with SessionLocal() as db:
        course = Course(teacher_id=teacher_id, title="Python", join_code=new_join_code(db))
        db.add(course)
        db.flush()
        for student_id in students:
            db.add(CourseEnrollment(course_id=course.id, student_id=student_id))
        db.commit()
        return course.id


def _attempt(headers: dict, course_id: int, section="m1-s1", activity="quiz", answers=None):
    body = {
        "section_id": section,
        "activity": activity,
        "course_id": course_id,
        "answers": answers
        if answers is not None
        else [{"question_index": 0, "prompt": "¿2+2?", "correct": True}],
    }
    return client.post("/api/analytics/attempts", json=body, headers=headers)


def _complete(headers: dict, course_id: int, section="m1-s1", activity="lectura"):
    body = {"section_id": section, "activity": activity, "course_id": course_id}
    return client.post("/api/analytics/completions", json=body, headers=headers)


def _classroom() -> tuple[int, dict, dict, int]:
    """Un estudiante inscrito en el curso de un docente."""
    student_id, student = _user("estudiante")
    teacher_id, teacher = _user("docente")
    return student_id, student, teacher, _course(teacher_id, student_id)


def _stored_answers(course_id: int) -> int:
    with SessionLocal() as db:
        return db.query(QuizAnswer).filter(QuizAnswer.course_id == course_id).count()


def _session_that_fails_on_commit():
    """Una sesión real cuyo commit falla como si la cuenta ya no existiera."""
    db = SessionLocal()

    def commit():
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    db.commit = commit
    try:
        yield db
    finally:
        db.close()


# --- registrar respuestas ----------------------------------------------------


def test_an_activity_without_questions_does_not_leave_answers():
    _, student, _, course_id = _classroom()

    response = _attempt(student, course_id, activity="lectura")

    assert response.status_code == 400
    assert "lectura" in response.json()["detail"]
    assert _stored_answers(course_id) == 0


def test_an_attempt_must_say_which_section_it_is_from():
    _, student, _, course_id = _classroom()

    response = _attempt(student, course_id, section="   ")

    assert response.status_code == 400
    assert _stored_answers(course_id) == 0


def test_an_attempt_without_answers_is_refused():
    _, student, _, course_id = _classroom()

    response = _attempt(student, course_id, answers=[])

    assert response.status_code == 400
    assert "sin respuestas" in response.json()["detail"]


def test_an_attempt_with_too_many_answers_is_refused():
    _, student, _, course_id = _classroom()
    answers = [{"question_index": i, "correct": True} for i in range(MAX_ANSWERS + 1)]

    response = _attempt(student, course_id, answers=answers)

    assert response.status_code == 413
    assert _stored_answers(course_id) == 0


def test_the_largest_allowed_attempt_is_stored_whole():
    _, student, _, course_id = _classroom()
    answers = [{"question_index": i, "correct": i % 2 == 0} for i in range(MAX_ANSWERS)]

    response = _attempt(student, course_id, answers=answers)

    assert response.status_code == 201
    assert response.json() == {"recorded": MAX_ANSWERS}
    assert _stored_answers(course_id) == MAX_ANSWERS


def test_a_forgotten_screen_does_not_spoil_the_average_time():
    _, student, teacher, course_id = _classroom()

    # Diez segundos de verdad y una pantalla abierta toda la tarde.
    _attempt(
        student,
        course_id,
        section="m7-s1",
        answers=[
            {"question_index": 0, "correct": True, "elapsed_ms": 10_000},
            {"question_index": 1, "correct": True, "elapsed_ms": 5 * 60 * 60 * 1000},
        ],
    )

    summary = client.get(f"/api/analytics/summary?course_id={course_id}", headers=teacher).json()
    section = next(s for s in summary["sections"] if s["section_id"] == "m7-s1")
    assert section["answered"] == 2
    assert section["avg_seconds"] == 10.0
    slow = next(q for q in summary["questions"] if q["question_index"] == 1)
    assert slow["avg_seconds"] is None


def test_answers_from_a_deleted_account_get_a_clear_conflict(monkeypatch):
    _, student, _, course_id = _classroom()
    monkeypatch.setitem(app.dependency_overrides, get_db, _session_that_fails_on_commit)

    response = _attempt(student, course_id)

    assert response.status_code == 409
    assert "ya no existe" in response.json()["detail"]
    assert _stored_answers(course_id) == 0


# --- actividades terminadas --------------------------------------------------


def test_an_unknown_activity_is_not_noted():
    _, student, _, course_id = _classroom()

    response = _complete(student, course_id, activity="siesta")

    assert response.status_code == 400
    assert "siesta" in response.json()["detail"]


def test_a_finished_activity_must_say_which_section_it_is_from():
    _, student, _, course_id = _classroom()

    response = _complete(student, course_id, section=" ")

    assert response.status_code == 400


def test_a_failed_note_does_not_bother_the_student(monkeypatch):
    student_id, student, _, course_id = _classroom()
    monkeypatch.setitem(app.dependency_overrides, get_db, _session_that_fails_on_commit)

    response = _complete(student, course_id)

    assert response.status_code == 200
    assert response.json() == {"recorded": False, "detail": "No se pudo anotar."}
    with SessionLocal() as db:
        noted = (
            db.query(ActivityCompletion)
            .filter(ActivityCompletion.usuario_id == student_id)
            .count()
        )
    assert noted == 0


# --- borrar lo registrado ----------------------------------------------------


def test_the_teacher_clears_one_section_of_their_course():
    student_id, student = _user("estudiante")
    teacher_id, teacher = _user("docente")
    course_id = _course(teacher_id, student_id)

    _attempt(student, course_id, section="m1-s1")
    _attempt(student, course_id, section="m1-s2")
    _complete(student, course_id, section="m1-s1")

    cleared = client.delete(
        f"/api/analytics/attempts?course_id={course_id}&section_id=m1-s1", headers=teacher
    )

    assert cleared.status_code == 200
    # Una respuesta y una actividad terminada de esa sección.
    assert cleared.json() == {"removed": 2}

    summary = client.get(f"/api/analytics/summary?course_id={course_id}", headers=teacher).json()
    assert [s["section_id"] for s in summary["sections"]] == ["m1-s2"]
    assert summary["total_completions"] == 0


def test_clearing_a_course_leaves_the_other_courses_alone():
    student_id, student = _user("estudiante")
    teacher_id, teacher = _user("docente")
    mine, other = _course(teacher_id, student_id), _course(teacher_id, student_id)

    _attempt(student, mine)
    _attempt(student, other)

    cleared = client.delete(f"/api/analytics/attempts?course_id={mine}", headers=teacher)

    assert cleared.json() == {"removed": 1}
    assert _stored_answers(mine) == 0
    assert _stored_answers(other) == 1


def test_a_teacher_cannot_clear_someone_elses_course():
    student_id, student = _user("estudiante")
    owner_id, _ = _user("docente")
    _, intruder = _user("docente")
    course_id = _course(owner_id, student_id)
    _attempt(student, course_id)

    response = client.delete(f"/api/analytics/attempts?course_id={course_id}", headers=intruder)

    assert response.status_code == 403
    assert _stored_answers(course_id) == 1


def test_a_student_cannot_clear_anything():
    _, student, _, course_id = _classroom()

    response = client.delete(f"/api/analytics/attempts?course_id={course_id}", headers=student)

    assert response.status_code == 403

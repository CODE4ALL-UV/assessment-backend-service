"""Las respuestas de los estudiantes y el resumen para el docente, curso a curso..."""

import uuid

from fastapi.testclient import TestClient

from assessment_service.main import app
from neon_storage import SessionLocal
from neon_storage.courses import general_course, new_join_code
from neon_storage.models import Course, CourseEnrollment, Usuario
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


def _answer(student: dict, course_id=None, section="m2-s1"):
    body = {
        "section_id": section,
        "activity": "quiz",
        "answers": [
            {"question_index": 0, "prompt": "¿2+2?", "correct": True, "elapsed_ms": 4000},
            {"question_index": 1, "prompt": "¿3*3?", "correct": False},
        ],
    }
    if course_id is not None:
        body["course_id"] = course_id
    return client.post("/api/analytics/attempts", json=body, headers=student)


def _section(summary: dict, section_id: str):
    return next((s for s in summary["sections"] if s["section_id"] == section_id), None)


def test_a_student_records_answers_and_their_teacher_sees_them():
    student_id, student = _user("estudiante")
    teacher_id, teacher = _user("docente")
    course_id = _course(teacher_id, student_id)

    recorded = _answer(student, course_id, section="m2-s1")
    assert recorded.status_code == 201
    assert recorded.json() == {"recorded": 2}

    summary = client.get(f"/api/analytics/summary?course_id={course_id}", headers=teacher).json()
    section = _section(summary, "m2-s1")
    assert section["answered"] == 2
    assert section["correct"] == 1


def test_each_teacher_only_sees_their_own_course():
    student_id, student = _user("estudiante")
    ana_id, _ = _user("docente")
    luis_id, luis = _user("docente")
    ana_course = _course(ana_id, student_id)
    luis_course = _course(luis_id, student_id)

    _answer(student, ana_course, section="m3-s1")

    luis_summary = client.get(
        f"/api/analytics/summary?course_id={luis_course}", headers=luis
    ).json()
    assert _section(luis_summary, "m3-s1") is None

    # Y no puede mirar el de Ana.
    peek = client.get(f"/api/analytics/summary?course_id={ana_course}", headers=luis)
    assert peek.status_code == 403


def test_nobody_records_answers_in_a_course_they_are_not_in():
    _, outsider = _user("estudiante")
    teacher_id, _ = _user("docente")
    course_id = _course(teacher_id)

    assert _answer(outsider, course_id).status_code == 403


def test_without_a_course_the_answers_go_to_the_general_one():
    _, student = _user("estudiante")
    _, director = _user("director")

    assert _answer(student, section="m4-s1").status_code == 201

    with SessionLocal() as db:
        general_id = general_course(db).id
    summary = client.get(
        f"/api/analytics/summary?course_id={general_id}", headers=director
    ).json()
    assert _section(summary, "m4-s1")["answered"] >= 2


def test_a_teacher_must_say_which_course():
    _, teacher = _user("docente")

    assert client.get("/api/analytics/summary", headers=teacher).status_code == 400


def test_the_coordination_can_see_everything_together():
    student_id, student = _user("estudiante")
    teacher_id, _ = _user("docente")
    _, director = _user("director")
    _answer(student, _course(teacher_id, student_id), section="m5-s1")

    summary = client.get("/api/analytics/summary", headers=director)

    assert summary.status_code == 200
    assert _section(summary.json(), "m5-s1") is not None


def test_the_same_activity_in_two_courses_counts_in_each():
    student_id, student = _user("estudiante")
    teacher_id, _ = _user("docente")
    first, second = _course(teacher_id, student_id), _course(teacher_id, student_id)

    def complete(course_id):
        body = {"section_id": "m2-s2", "activity": "lectura", "course_id": course_id}
        return client.post("/api/analytics/completions", json=body, headers=student).json()

    assert complete(first)["recorded"] is True
    assert complete(first)["recorded"] is False
    assert complete(second)["recorded"] is True


def test_the_student_list_is_the_course_list():
    inside_id, _ = _user("estudiante")
    outside_id, _ = _user("estudiante")
    teacher_id, teacher = _user("docente")
    course_id = _course(teacher_id, inside_id)

    listed = client.get(f"/api/analytics/students?course_id={course_id}", headers=teacher).json()
    ids = [s["user_id"] for s in listed["students"]]

    assert inside_id in ids
    assert outside_id not in ids


def test_a_student_cannot_read_the_course_summary():
    _, student = _user("estudiante")

    assert client.get("/api/analytics/summary", headers=student).status_code == 403

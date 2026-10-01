"""Qué están aprendiendo los estudiantes, y qué se les atraganta.

Aquí se guarda una fila por **respuesta**, no una nota por lección. Es la
diferencia entre saber que un tema va mal y saber qué pregunta concreta falla
todo el mundo, que es lo que el docente necesita para arreglarlo.

Privacidad: se guarda si la respuesta fue correcta, no cuál eligió el
estudiante. Para saber qué tema cuesta más no hace falta más que eso, y no
guardar lo innecesario es lo correcto.

Quién ve qué: el estudiante solo puede registrar lo suyo; los resúmenes de
todo el curso son solo para docentes.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Integer, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from neon_storage import get_db
from neon_storage.models import (
    ActivityCompletion,
    QuizAnswer,
    Usuario,
)

from user_management_service.auth import Caller, current_caller, require_course_editor

router = APIRouter(prefix="/api/analytics", tags=["Analítica"])

# Actividades con preguntas, que son las que dejan respuestas.
ANSWER_ACTIVITIES = {"quiz", "evaluacion", "ejercicio"}

# Todas las actividades de una seccion. Registrar tambien las que no
# tienen preguntas es lo que permite saber hasta donde ha llegado cada
# estudiante, y no solo como le fue en los quiz.
ALL_ACTIVITIES = {
    "lectura",
    "capsula",
    "ejemplo",
    "ejercicio",
    "quiz",
    "evaluacion",
    "video",
    "laboratorio",
}

# Tope por envío: una sección no tiene cientos de preguntas, y así un cliente
# roto no puede llenar la base de una sentada.
MAX_ANSWERS = 60


# Tope de tiempo por pregunta: media hora. Por encima de eso lo que hubo fue
# una pantalla abierta y olvidada, no alguien pensando, y meterlo en la media
# la estropea.
MAX_ELAPSED_MS = 30 * 60 * 1000


class AnswerIn(BaseModel):
    question_index: int = Field(..., ge=0)
    prompt: str = ""
    correct: bool
    elapsed_ms: Optional[int] = Field(default=None, ge=0)


class AttemptIn(BaseModel):
    section_id: str
    activity: str
    answers: list[AnswerIn]


@router.post("/attempts", status_code=status.HTTP_201_CREATED)
def record_attempt(
    payload: AttemptIn,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """Guarda lo que un estudiante acaba de responder.

    El estudiante solo puede registrar lo suyo: el id sale del token, no del
    cuerpo de la petición. Si viniera del cuerpo, cualquiera podría inventar
    respuestas a nombre de otro.
    """
    if payload.activity not in ANSWER_ACTIVITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Actividad desconocida: {payload.activity}",
        )
    if not payload.section_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta decir de qué sección es el intento.",
        )
    if not payload.answers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El intento llegó sin respuestas.",
        )
    if len(payload.answers) > MAX_ANSWERS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Demasiadas respuestas en un solo intento.",
        )

    for answer in payload.answers:
        db.add(
            QuizAnswer(
                usuario_id=caller.user_id,
                section_id=payload.section_id.strip(),
                activity=payload.activity,
                question_index=answer.question_index,
                prompt=answer.prompt[:500],
                correct=answer.correct,
                elapsed_ms=(
                    answer.elapsed_ms
                    if answer.elapsed_ms is not None
                    and answer.elapsed_ms <= MAX_ELAPSED_MS
                    else None
                ),
            )
        )

    try:
        db.commit()
    except IntegrityError:
        # Pasa si la cuenta ya no existe: una sesión vieja de alguien a quien
        # borraron. Vale más decirlo que devolver un error del servidor, que
        # en la aplicación se vería como «algo falló» sin más.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tu cuenta ya no existe. Vuelve a iniciar sesión.",
        )

    return {"recorded": len(payload.answers)}


class CompletionIn(BaseModel):
    section_id: str
    activity: str


@router.post("/completions", status_code=status.HTTP_200_OK)
def record_completion(
    payload: CompletionIn,
    db: Session = Depends(get_db),
    caller: Caller = Depends(current_caller),
):
    """Anota que un estudiante terminó una actividad.

    Terminar dos veces lo mismo no cuenta dos veces: lo que importa es si la
    hizo, no cuántas. Por eso repetirlo devuelve el mismo resultado en vez de
    un error, que para el cliente es más sencillo de tratar.
    """
    if payload.activity not in ALL_ACTIVITIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Actividad desconocida: {payload.activity}",
        )
    if not payload.section_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Falta decir de qué sección es la actividad.",
        )

    section_id = payload.section_id.strip()

    existing = (
        db.query(ActivityCompletion)
        .filter(
            ActivityCompletion.usuario_id == caller.user_id,
            ActivityCompletion.section_id == section_id,
            ActivityCompletion.activity == payload.activity,
        )
        .first()
    )
    if existing is not None:
        return {"recorded": False, "detail": "Ya estaba anotada."}

    db.add(
        ActivityCompletion(
            usuario_id=caller.user_id,
            section_id=section_id,
            activity=payload.activity,
        )
    )

    try:
        db.commit()
    except IntegrityError:
        # O la cuenta ya no existe, o dos pantallas lo mandaron a la vez. En
        # ninguno de los dos casos hay nada que arreglar por parte de quien
        # estudia, asi que no se le molesta con un error.
        db.rollback()
        return {"recorded": False, "detail": "No se pudo anotar."}

    return {"recorded": True}


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    _teacher: Caller = Depends(require_course_editor),
):
    """Cómo va el curso, sección por sección y pregunta por pregunta.

    Una lista vacía significa que todavía nadie ha respondido nada, que es el
    estado normal al principio. No es un error, y conviene que la pantalla lo
    diga así en vez de enseñar ceros que parecen datos.
    """
    total = db.query(func.count(QuizAnswer.id)).scalar() or 0

    # --- por sección --------------------------------------------------------
    por_seccion = (
        db.query(
            QuizAnswer.section_id,
            func.count(QuizAnswer.id).label("total"),
            func.sum(func.cast(QuizAnswer.correct, Integer)).label("aciertos"),
            func.count(func.distinct(QuizAnswer.usuario_id)).label("estudiantes"),
            func.avg(QuizAnswer.elapsed_ms).label("tiempo"),
        )
        .group_by(QuizAnswer.section_id)
        .all()
    )

    sections = []
    for row in por_seccion:
        answered = int(row.total or 0)
        correct = int(row.aciertos or 0)
        sections.append(
            {
                "section_id": row.section_id,
                "answered": answered,
                "correct": correct,
                "failed": answered - correct,
                "students": int(row.estudiantes or 0),
                "accuracy": round(correct / answered, 4) if answered else 0.0,
                "avg_seconds": (
                    round(float(row.tiempo) / 1000, 1)
                    if row.tiempo is not None
                    else None
                ),
            }
        )

    # --- por pregunta -------------------------------------------------------
    por_pregunta = (
        db.query(
            QuizAnswer.section_id,
            QuizAnswer.activity,
            QuizAnswer.question_index,
            func.max(QuizAnswer.prompt).label("prompt"),
            func.count(QuizAnswer.id).label("total"),
            func.sum(func.cast(QuizAnswer.correct, Integer)).label("aciertos"),
            func.avg(QuizAnswer.elapsed_ms).label("tiempo"),
        )
        .group_by(
            QuizAnswer.section_id,
            QuizAnswer.activity,
            QuizAnswer.question_index,
        )
        .all()
    )

    questions = []
    for row in por_pregunta:
        answered = int(row.total or 0)
        correct = int(row.aciertos or 0)
        questions.append(
            {
                "section_id": row.section_id,
                "activity": row.activity,
                "question_index": row.question_index,
                "prompt": row.prompt or "",
                "answered": answered,
                "correct": correct,
                "failed": answered - correct,
                "accuracy": round(correct / answered, 4) if answered else 0.0,
                # Nulo cuando ninguna de esas respuestas trae tiempo medido.
                "avg_seconds": (
                    round(float(row.tiempo) / 1000, 1)
                    if row.tiempo is not None
                    else None
                ),
            }
        )

    # --- que actividades se terminan ---------------------------------------
    por_actividad = (
        db.query(
            ActivityCompletion.section_id,
            ActivityCompletion.activity,
            func.count(ActivityCompletion.id).label("total"),
        )
        .group_by(ActivityCompletion.section_id, ActivityCompletion.activity)
        .all()
    )

    completions = [
        {
            "section_id": row.section_id,
            "activity": row.activity,
            "students": int(row.total or 0),
        }
        for row in por_actividad
    ]

    total_completions = db.query(func.count(ActivityCompletion.id)).scalar() or 0

    return {
        "total_answers": total,
        "total_completions": int(total_completions),
        "sections": sections,
        "questions": questions,
        "completions": completions,
    }


@router.get("/students")
def students(
    db: Session = Depends(get_db),
    _teacher: Caller = Depends(require_course_editor),
):
    """Los estudiantes y cómo les está yendo.

    Salen todos, también quien no ha respondido nada: justo esos son los que
    el docente necesita ver.
    """
    alumnos = (
        db.query(Usuario)
        .filter(func.lower(Usuario.rol) == "estudiante")
        .order_by(Usuario.nombre)
        .all()
    )

    stats = dict(
        (
            row.usuario_id,
            {
                "answered": int(row.total or 0),
                "correct": int(row.aciertos or 0),
                "sections": int(row.secciones or 0),
            },
        )
        for row in db.query(
            QuizAnswer.usuario_id,
            func.count(QuizAnswer.id).label("total"),
            func.sum(func.cast(QuizAnswer.correct, Integer)).label("aciertos"),
            func.count(func.distinct(QuizAnswer.section_id)).label("secciones"),
        )
        .group_by(QuizAnswer.usuario_id)
        .all()
    )

    hechas = dict(
        (row.usuario_id, int(row.total or 0))
        for row in db.query(
            ActivityCompletion.usuario_id,
            func.count(ActivityCompletion.id).label("total"),
        )
        .group_by(ActivityCompletion.usuario_id)
        .all()
    )

    out = []
    for alumno in alumnos:
        mine = stats.get(alumno.id_usuario)
        answered = mine["answered"] if mine else 0
        correct = mine["correct"] if mine else 0

        out.append(
            {
                "user_id": alumno.id_usuario,
                "nombre": alumno.nombre,
                "correo": alumno.correo,
                "answered": answered,
                "correct": correct,
                "failed": answered - correct,
                "sections_touched": mine["sections"] if mine else 0,
                "activities_done": hechas.get(alumno.id_usuario, 0),
                "accuracy": round(correct / answered, 4) if answered else 0.0,
            }
        )

    return {"count": len(out), "students": out}


@router.delete("/attempts")
def clear_attempts(
    section_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _teacher: Caller = Depends(require_course_editor),
):
    """Borra lo registrado, entero o de una sección.

    Sirve para limpiar los datos de una prueba antes de empezar un curso de
    verdad. Borra respuestas de estudiantes, así que solo un docente puede.
    """
    answers = db.query(QuizAnswer)
    done = db.query(ActivityCompletion)
    if section_id:
        answers = answers.filter(QuizAnswer.section_id == section_id)
        done = done.filter(ActivityCompletion.section_id == section_id)

    removed = answers.delete(synchronize_session=False)
    removed += done.delete(synchronize_session=False)
    db.commit()

    return {"removed": removed}

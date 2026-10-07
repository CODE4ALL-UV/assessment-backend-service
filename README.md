# assessment-backend-service · Ejercicios y evaluación

Este es el microservicio de **ejercicios y evaluación** de Code4All. Guarda lo
que pasa cuando un estudiante responde: **si acertó o no** cada pregunta, cuánto
se demoró y qué actividades del curso terminó. Con eso le arma al docente un
resumen de su curso para que vea, por ejemplo, **qué pregunta concreta está
fallando todo el mundo** y en qué sección se está quedando la gente.

Todo va **por curso**: el docente solo ve los datos de sus cursos, y lo que
llega sin curso se cuenta en el Curso general.

## Lo que este servicio no hace: calificar

El backend **no corrige**. No ejecuta código Python, no compara salidas ni
revisa opciones: la app ya sabe cuál es la respuesta correcta, corrige ahí
mismo para darle retroalimentación inmediata al estudiante y después manda aquí
el resultado (`correct: true` o `false`).

Tampoco se guarda **qué** contestó el estudiante, solo si acertó. Para
entender dónde se traba un grupo basta con eso, y así no se guarda más
información personal de la necesaria.

## Las rutas

Todas piden el token en la cabecera `Authorization: Bearer <token>`.

| Ruta | Qué hace | Quién |
|---|---|---|
| `POST /api/analytics/attempts` | Registra las respuestas de un intento. | Quien puede ver el curso (normalmente, el estudiante inscrito) |
| `POST /api/analytics/completions` | Anota que se terminó una actividad. | Quien puede ver el curso |
| `GET /api/analytics/summary` | El resumen del curso: por sección, por pregunta y por actividad. | Docente del curso o dirección |
| `GET /api/analytics/students` | La lista de estudiantes del curso con sus números. | Docente del curso o dirección |
| `DELETE /api/analytics/attempts` | Borra respuestas y actividades terminadas (para limpiar datos de prueba). | Docente del curso o dirección |

### Registrar un intento — `POST /api/analytics/attempts`

Cuando el estudiante termina un quiz, una evaluación o un ejercicio, la app
manda una línea por pregunta:

```json
{
  "course_id": 7,
  "section_id": "m2-s1",
  "activity": "quiz",
  "answers": [
    { "question_index": 0, "prompt": "¿Qué imprime print(2 + 3)?", "correct": true,  "elapsed_ms": 8200 },
    { "question_index": 1, "prompt": "¿Cuál es un nombre de variable válido?", "correct": false, "elapsed_ms": 15400 }
  ]
}
```

Responde 201 con `{ "recorded": 2 }`.

- `activity` puede ser `quiz`, `evaluacion` o `ejercicio`.
- `course_id` es opcional. Sin él, la respuesta va al Curso general.
- Hasta **60 respuestas** por envío.
- `prompt` es el enunciado. Se copia (hasta 500 caracteres) para que, si el
  docente edita la pregunta después, siga quedando lo que se preguntó en su
  momento.
- `elapsed_ms` es opcional. Si pasa de 30 minutos se descarta: seguro el
  estudiante dejó la pestaña abierta, y ese tiempo dañaría el promedio.
- **El id del estudiante sale del token, nunca del cuerpo.** Nadie puede
  registrar respuestas a nombre de otro.
- Solo se puede registrar en un curso que se puede ver: un estudiante no
  inscrito recibe 403.
- Cada intento suma filas nuevas. Si el estudiante repite el quiz, cuentan los
  dos intentos.

| Código | Cuándo |
|---|---|
| 400 | La actividad no existe, falta la sección o no llegó ninguna respuesta. |
| 403 | No puede ver ese curso (por ejemplo, no está inscrito). |
| 404 | El curso no existe. |
| 409 | La cuenta del token ya no existe. |
| 413 | Llegaron más de 60 respuestas. |

### Anotar una actividad terminada — `POST /api/analytics/completions`

```json
{ "course_id": 7, "section_id": "m2-s1", "activity": "video" }
```

`activity` puede ser `lectura`, `capsula`, `ejemplo`, `ejercicio`, `quiz`,
`evaluacion`, `video` o `laboratorio`.

Se guarda una sola vez por estudiante, curso, sección y actividad. Si ya
estaba anotada, responde `{ "recorded": false, "detail": "Ya estaba anotada." }`
sin error. Si el mismo estudiante está en dos cursos, cuenta por separado en
cada uno.

### El resumen del curso — `GET /api/analytics/summary?course_id=7`

```json
{
  "course_id": 7,
  "total_answers": 340,
  "total_completions": 128,
  "sections": [
    { "section_id": "m2-s1", "answered": 120, "correct": 84, "failed": 36, "students": 30, "accuracy": 0.7, "avg_seconds": 11.3 }
  ],
  "questions": [
    { "section_id": "m2-s1", "activity": "quiz", "question_index": 1, "prompt": "¿Cuál es un nombre de variable válido?",
      "answered": 60, "correct": 21, "failed": 39, "accuracy": 0.35, "avg_seconds": 15.8 }
  ],
  "completions": [
    { "section_id": "m2-s1", "activity": "video", "students": 28 }
  ]
}
```

- `accuracy` es una fracción entre 0 y 1 (0.35 = 35 % de aciertos).
- `avg_seconds` es el tiempo promedio por respuesta, o `null` si no hay
  tiempos.
- `students` en cada sección es cuántos estudiantes distintos respondieron.
- Al principio todo viene vacío, y eso es normal.

### Los estudiantes del curso — `GET /api/analytics/students?course_id=7`

La lista de estudiantes inscritos, ordenada por nombre, con `answered`,
`correct`, `failed`, `accuracy`, `sections_touched` (en cuántas secciones
respondió algo) y `activities_done` (cuántas actividades terminó). Salen
también los que todavía no han hecho nada, con ceros. En el Curso general
salen todos los estudiantes.

Esta lista es la que da el `user_id` que se necesita para sacar a un
estudiante de un curso (`DELETE /api/courses/{id}/students/{student_id}`, en
course-content).

### Borrar datos — `DELETE /api/analytics/attempts?course_id=7&section_id=m2-s1`

Borra las respuestas y las actividades terminadas del curso, y si se manda
`section_id`, solo las de esa sección. Responde `{ "removed": n }`. Está
pensado para limpiar lo que queda de las pruebas antes de usar un curso con
estudiantes de verdad.

> **Cuidado:** si la dirección llama a esta ruta **sin** `course_id`, se borran
> los datos de **todos los cursos**. No se puede deshacer.

### Qué ve cada uno

Para `summary`, `students` y el `DELETE`:

- **Docente:** tiene que decir de qué curso (`course_id`), y solo puede ver los
  suyos. Sin `course_id` responde 400; con un curso ajeno, 403.
- **Dirección:** puede ver cualquier curso. Sin `course_id` ve **todos los
  cursos juntos**. El Curso general solo lo ve la dirección.
- **Estudiante:** no puede ver estas rutas (403).

Las reglas de acceso no se repiten aquí: se importan de
`course_content_service/access.py`, en
[course-content](https://github.com/CODE4ALL-UV/course-content-backend-service).
Por eso este servicio necesita ese repositorio para funcionar.

## Dónde lo usa la app

En el [Front-end](https://github.com/CODE4ALL-UV/Front-end),
`lib/data/services/learning_analytics_service.dart` manda los intentos y las
actividades terminadas. Lo hace sin hacer esperar al estudiante: si el envío
falla, el estudiante sigue con su curso normalmente. Las estadísticas del panel
del docente y de la dirección las pide `lib/data/course/course_analytics_store.dart`
con `summary` y `students`.

## Tablas que usa

Están definidas en
[neon-storage](https://github.com/CODE4ALL-UV/neon-storage-backend-service).

| Tabla | Qué hace con ella |
|---|---|
| `QuizAnswer` | Una fila por pregunta respondida: estudiante, curso, sección, actividad, número de pregunta, enunciado, si acertó y cuánto tardó. La escribe, la resume y la borra. |
| `ActivityCompletion` | Una fila por actividad terminada (estudiante, curso, sección, actividad). La escribe, la resume y la borra. |
| `Usuario` | Lee la lista de estudiantes. |
| `CourseEnrollment` | Lee quién está inscrito en cada curso. |
| `Course` | Lee el curso (a través de las reglas de course-content). Si el Curso general no existe todavía, lo crea. |

## Cómo lo monta el gateway

Todos los servicios de Code4All siguen el mismo contrato:
`assessment_service/routes.py` tiene una función `register(app)` que añade las
rutas de este servicio a una aplicación de FastAPI. El
[gateway](https://github.com/CODE4ALL-UV/Back-end) trae este repositorio como
submódulo en `services/` y llama a esa función al arrancar.
`assessment_service/main.py` hace lo mismo para correrlo solo.

Depende de tres repositorios: **neon-storage** (la base de datos),
**user-management** (la sesión) y **course-content** (las reglas de acceso a
cada curso).

## Estructura

```
assessment_service/
├── routes.py                    register(app): lo único que llama el gateway
├── main.py                      arranque independiente (lee el .env y prepara la base)
└── presentation/api/
    └── analytics_routes.py      las cinco rutas de /api/analytics
tests/
├── conftest.py
└── test_analytics_routes.py
```

## Variables de entorno

| Variable | Para qué | ¿Obligatoria? |
|---|---|---|
| `DATABASE_URL` | La cadena de conexión de Neon. | Sí |
| `SECRET_KEY` | Para comprobar los tokens. Tiene que ser **la misma** que usa user-management. | Sí |
| `ALGORITHM` | Algoritmo del token. Por defecto `HS256`. | No |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Duración del token. Aquí solo se usa en las pruebas. | No |

Están en `.env.example`.

## Correrlo

Lo normal es correrlo dentro del gateway (repo `Back-end`), que monta todos
los servicios juntos. Para correrlo solo hacen falta `neon-storage`,
`user-management` y `course-content` clonados al lado:

```powershell
$env:PYTHONPATH = "..\neon-storage-backend-service;..\user-management-backend-service;..\course-content-backend-service"
pip install -r requirements.txt -r ..\neon-storage-backend-service\requirements.txt -r ..\user-management-backend-service\requirements.txt -r ..\course-content-backend-service\requirements.txt
copy .env.example .env
uvicorn assessment_service.main:app --reload
```

## Pruebas

```powershell
pytest tests
```

No tocan Neon: `conftest.py` fuerza una base SQLite temporal y busca los repos
hermanos, así que funcionan igual con los repos clonados uno al lado del otro
que dentro de `services/` del gateway.

`test_analytics_routes.py` (9 pruebas) comprueba que:

- un estudiante registra sus respuestas y su docente las ve en el resumen;
- cada docente ve solo su curso;
- nadie registra respuestas en un curso en el que no está;
- sin `course_id` las respuestas van al Curso general y la dirección las ve;
- un docente sin `course_id` recibe 400, y la dirección ve el total de todos
  los cursos;
- la misma actividad terminada en dos cursos cuenta en cada uno, y repetirla
  no la duplica;
- la lista de estudiantes es la del curso;
- un estudiante no puede leer el resumen.

Además, `tests/test_gateway_flow.py` del gateway prueba el recorrido completo
con course-content: el docente crea un curso, el estudiante se inscribe y
responde, y el docente ve la respuesta.

En GitHub, cada push o pull request a `main` corre las pruebas con cobertura y
la sube a Codacy (`.github/workflows/codacy-coverage.yml`).

## Cosas a tener en cuenta

- El servidor confía en lo que manda la app sobre si la respuesta es correcta
  y cuánto tardó. Es suficiente para estadísticas de clase, pero no sirve como
  nota oficial.
- El resumen cuenta todos los intentos, no solo el último ni el mejor.
- Si un estudiante sale de un curso, sus respuestas siguen contando en el
  resumen, pero ya no aparece en la lista de estudiantes.
- No hay una ruta para que el estudiante consulte su propio avance: la app lo
  lleva en el dispositivo.
- Las mismas rutas, sin cursos, siguen en el monolito de user-management
  mientras no se haga el corte en Render.

## Los repositorios de Code4All

| Parte | Repositorio |
|---|---|
| App (Flutter) | [Front-end](https://github.com/CODE4ALL-UV/Front-end) |
| API Gateway | [Back-end](https://github.com/CODE4ALL-UV/Back-end) |
| Gestión de usuarios | [user-management-backend-service](https://github.com/CODE4ALL-UV/user-management-backend-service) |
| Curso y contenidos de Python | [course-content-backend-service](https://github.com/CODE4ALL-UV/course-content-backend-service) |
| **Ejercicios y evaluación** | **este repositorio** |
| Progreso y seguimiento | [progress-tracking-backend-service](https://github.com/CODE4ALL-UV/progress-tracking-backend-service) |
| Accesibilidad y adaptación | [accessibility-backend-service](https://github.com/CODE4ALL-UV/accessibility-backend-service) |
| Interacción multimodal | [multimodal-interaction-backend-service](https://github.com/CODE4ALL-UV/multimodal-interaction-backend-service) |
| Infraestructura y dispositivos | [device-management-backend-service](https://github.com/CODE4ALL-UV/device-management-backend-service) |
| Capa de datos compartida (Neon) | [neon-storage-backend-service](https://github.com/CODE4ALL-UV/neon-storage-backend-service) |

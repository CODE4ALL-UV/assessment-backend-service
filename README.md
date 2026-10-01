# assessment-backend-service
Repositorio Back-end para el módulo Ejercicios y Evaluación.

Guarda cada respuesta que da un estudiante (si acertó, no qué contestó) y qué
actividades termina, y le da al docente el resumen: qué sección y qué pregunta
concreta está fallando la gente.

| Rutas | Quién |
|---|---|
| `POST /api/analytics/attempts`, `POST /api/analytics/completions` | Estudiante con sesión (lo suyo) |
| `GET /api/analytics/summary`, `GET /api/analytics/students` | Docente o director |
| `DELETE /api/analytics/attempts` | Docente o director |

**Tablas que escribe:** `QuizAnswer`, `ActivityCompletion`. Lee `Usuario`
para listar a los estudiantes.

**Sesión:** con `user_management_service.auth`. El id del estudiante sale del
token, nunca del cuerpo de la petición.

## Cómo lo monta el gateway

`assessment_service/routes.py` tiene `register(app)`, que añade las rutas de este
servicio a una aplicación de FastAPI. El gateway lo llama para cada servicio, y
`assessment_service/main.py` hace lo mismo para arrancarlo solo.

## Correrlo

Lo normal es correrlo dentro del gateway (repo `Back-end`), que monta todos
los servicios juntos. Para correrlo solo hacen falta `neon-storage` y
`user-management` clonados al lado:

```powershell
$env:PYTHONPATH = "..\neon-storage-backend-service;..\user-management-backend-service"
pip install -r requirements.txt -r ..\neon-storage-backend-service\requirements.txt -r ..\user-management-backend-service\requirements.txt
copy .env.example .env
uvicorn assessment_service.main:app --reload
```

## Pruebas

```bash
pytest tests
```

No tocan Neon: usan una base SQLite temporal. Buscan `neon-storage` y
`user-management` en los repos hermanos, así que funcionan igual con los repos
clonados uno al lado del otro que dentro de `services/` del gateway.

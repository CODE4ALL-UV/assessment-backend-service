"""Lo que este microservicio aporta al API Gateway.

El gateway (repo Back-end) llama a `register(app)` de cada servicio, y el
`main.py` de este paquete hace lo mismo para arrancarlo solo. Así las rutas se
declaran una sola vez, se arranque como se arranque.
"""

from fastapi import FastAPI

from assessment_service.presentation.api.analytics_routes import router as analytics_router


def register(app: FastAPI) -> None:
    app.include_router(analytics_router)

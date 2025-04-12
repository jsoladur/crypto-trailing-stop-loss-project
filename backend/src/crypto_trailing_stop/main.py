from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from crypto_trailing_stop.config import get_configuration_properties
from crypto_trailing_stop.infrastructure.tasks import TaskManager
from crypto_trailing_stop.interfaces.controllers.health_controller import (
    router as health_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    TaskManager()
    yield


app = FastAPI(lifespan=lifespan)
configuration_properties = get_configuration_properties()
if configuration_properties.cors_enabled:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
app.include_router(health_router)

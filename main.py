from contextlib import asynccontextmanager
from fastapi import FastAPI
from database.database import init_db
from routes.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="ASR Pipeline", lifespan=lifespan)
app.include_router(router)

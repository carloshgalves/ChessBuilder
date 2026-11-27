from contextlib import asynccontextmanager
from fastapi import FastAPI
from chessbuilder.db import connect, migrate

@asynccontextmanager
async def lifespan(app):
    migrate()
    from chessbuilder.engine import Worker
    worker=Worker()
    worker.start()
    try:
        yield
    finally:
        worker.stop()

app = FastAPI(title="Chess Builder", lifespan=lifespan)

@app.get("/api/health")
def health():
    with connect() as conn:
        return {"status":"ok","schema":conn.execute("PRAGMA user_version").fetchone()[0]}

@app.get("/api/profile")
def profile():
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM profiles WHERE id=1").fetchone())

from chessbuilder.importer import router as import_router
app.include_router(import_router)

from chessbuilder.engine import router as engine_router
app.include_router(engine_router)

from chessbuilder.review import router as review_router
app.include_router(review_router)

from chessbuilder.dashboard import router as dashboard_router
app.include_router(dashboard_router)

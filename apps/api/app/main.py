import json
import logging
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


configure_logging()
logger = logging.getLogger("api")


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


GIT_SHA = _git_sha()
VERSION = "0.1.0-phase0"

app = FastAPI(title="Adaptive Study Platform API", version=VERSION)

from .routers.documents import router as documents_router  # noqa: E402

app.include_router(documents_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error path=%s err=%s", request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "git_sha": GIT_SHA}


@app.get("/")
def root() -> dict:
    return {"service": "study-api", "version": VERSION}

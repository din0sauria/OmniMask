import getpass
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import fs, policies, tasks, quality, maintenance
from .core.config import DATA_DIR, KEYLEAK_DIR, ROOT

@asynccontextmanager
async def lifespan(app):
    from .services.jobs import recover_interrupted
    recover_interrupted()
    yield


app = FastAPI(title="面向多模态数据的隐私脱敏系统 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000",
                   "http://localhost:3001", "http://127.0.0.1:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(policies.router)
app.include_router(fs.router)
app.include_router(quality.router)
app.include_router(maintenance.router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.status_code, "msg": exc.detail, "data": None})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"code": 422, "msg": "请求参数有误", "data": exc.errors()})


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"code": 500, "msg": f"服务器错误：{exc}", "data": None})


@app.get("/api/env")
def env():
    home = Path.home()
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "user": getpass.getuser(),
            "home": str(home),
            "cwd": str(Path.cwd()),
        },
    }


@app.get("/api/health")
def health():
    return {
        "code": 0,
        "msg": "ok",
        "data": {
            "service": "omnimask",
            "root": str(ROOT),
            "data_dir": str(DATA_DIR),
            "keyleak": KEYLEAK_DIR.exists(),
            "rules": (KEYLEAK_DIR / "data" / "regex" / "merged_rules.json").exists(),
        },
    }

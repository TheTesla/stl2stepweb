import os
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Cookie, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.config import get_settings, ensure_dirs
from app.models import SessionManager, JobStatus
from app.worker import ConversionWorker

BASE_DIR = Path(__file__).resolve().parent.parent
# Create Jinja2 environment with cache_size=0 to avoid unhashable type errors
# The request object isn't hashable, so we disable template caching
jinja_env = Environment(loader=FileSystemLoader(str(BASE_DIR / "templates")), cache_size=0)
templates = Jinja2Templates(env=jinja_env)

session_manager: SessionManager = None
worker: ConversionWorker = None
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_manager, worker
    ensure_dirs(settings)
    session_manager = SessionManager(settings)
    worker = ConversionWorker(session_manager)
    await worker.start()
    yield
    await worker.stop()


app = FastAPI(title="STL to STEP Converter", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")


def get_or_create_session(session_id: str | None = None) -> str:
    if session_id and session_manager.get_session(session_id):
        session_manager.keep_alive(session_id)
        return session_id
    session = session_manager.create_session()
    return session.id


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session_id: str | None = Cookie(default=None)):
    session_id = get_or_create_session(session_id)
    response = templates.TemplateResponse("index.html", {"request": request})
    response.set_cookie(key="session_id", value=session_id, httponly=True, max_age=settings.session.max_file_retention_seconds)
    return response


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    session_id: str | None = Cookie(default=None)
):
    session_id = get_or_create_session(session_id)
    session = session_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=400, detail="Invalid session")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    ext = Path(file.filename).suffix.lower()
    if ext not in [e.lower() for e in settings.upload.allowed_extensions]:
        raise HTTPException(status_code=400, detail="Only STL files allowed")

    max_size = settings.upload.max_file_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"File too large (max {settings.upload.max_file_size_mb}MB)")

    upload_dir = Path(settings.storage.upload_dir) / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    upload_path = upload_dir / f"{uuid.uuid4()}{ext}"
    with open(upload_path, "wb") as f:
        f.write(content)

    job = session_manager.add_job(session_id, file.filename, str(upload_path), len(content))
    
    return JSONResponse({
        "job_id": job.id,
        "status": job.status.value,
        "position_in_queue": job.position_in_queue,
        "message": "File uploaded successfully"
    })


@app.get("/status/{job_id}")
async def get_status(job_id: str, session_id: str | None = Cookie(default=None)):
    session_id = get_or_create_session(session_id)
    job = session_manager.get_job(job_id)
    
    if not job or job.session_id != session_id:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status == JobStatus.QUEUED:
        job.position_in_queue = session_manager.get_queue_status(job_id) or 0

    return JSONResponse(job.to_dict())


@app.get("/jobs")
async def list_jobs(session_id: str | None = Cookie(default=None)):
    session_id = get_or_create_session(session_id)
    jobs = session_manager.get_session_jobs(session_id)
    return JSONResponse([job.to_dict() for job in jobs])


@app.get("/download/{job_id}")
async def download_file(job_id: str, session_id: str | None = Cookie(default=None)):
    session_id = get_or_create_session(session_id)
    job = session_manager.get_job(job_id)
    
    if not job or job.session_id != session_id:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.COMPLETED or not job.output_path:
        raise HTTPException(status_code=400, detail="File not ready")

    output_path = Path(job.output_path)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    session_manager.keep_alive(session_id)
    
    return FileResponse(
        path=output_path,
        filename=f"{Path(job.original_filename).stem}.step",
        media_type="application/step"
    )


@app.post("/keepalive")
async def keep_alive(session_id: str | None = Cookie(default=None)):
    if session_id and session_manager.keep_alive(session_id):
        return JSONResponse({"status": "ok"})
    raise HTTPException(status_code=400, detail="Invalid session")


@app.get("/api/stats")
async def get_stats():
    return JSONResponse(session_manager.get_stats())


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
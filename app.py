from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from system_metrics import collect_status


BASE_DIR = Path(__file__).parent


def create_app(
    status_provider: Callable[[], dict] = collect_status,
    template_dir: Path | None = None,
) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(template_dir or BASE_DIR / "templates"))

    @application.get("/")
    def dashboard(request: Request):
        return templates.TemplateResponse(request=request, name="dashboard.html", context={})

    @application.get("/api/status")
    def api_status():
        return JSONResponse(status_provider(), headers={"Cache-Control": "no-store"})

    @application.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    return application


app = create_app()

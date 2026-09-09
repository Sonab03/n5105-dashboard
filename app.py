from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from system_metrics import collect_status, load_service_config


BASE_DIR = Path(__file__).parent
SERVICE_CONFIG_PATH = BASE_DIR / "config" / "services.json"
APP_VERSION = "1.1.1"


def create_app(
    status_provider: Callable[[], dict] | None = None,
    template_dir: Path | None = None,
    service_config_path: Path = SERVICE_CONFIG_PATH,
    status_collector: Callable[..., dict] = collect_status,
) -> FastAPI:
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(template_dir or BASE_DIR / "templates"))
    if status_provider is None:
        service_targets, service_config_errors = load_service_config(service_config_path)

        def configured_status_provider():
            return status_collector(
                service_targets=service_targets,
                service_config_errors=service_config_errors,
            )

        selected_status_provider = configured_status_provider
    else:
        selected_status_provider = status_provider

    @application.get("/")
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"app_version": APP_VERSION},
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/status")
    def api_status():
        return JSONResponse(
            selected_status_provider(), headers={"Cache-Control": "no-store"}
        )

    @application.get("/healthz")
    def healthz():
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    return application


app = create_app()

from __future__ import annotations

import asyncio
import hmac
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.errors import AppError, DiscoveryUnavailableError
from app.schemas import (
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ProspectDiscoveryRequest,
    ProspectDiscoveryResponse,
)

logger = logging.getLogger("app")

API_DESCRIPTION = """Finds public prospect profiles across Google, LinkedIn, X, YouTube, Instagram,
Facebook and Threads from a natural-language query, consolidates profiles that belong to the same
person or business, and returns only matches backed by public evidence. Email, phone and WhatsApp
details appear only when the prospect has published them."""

_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


class Unauthorized(AppError):
    code = "unauthorized"
    status_code = 401
    public_message = "A valid X-API-Key header is required."


class RateLimited(AppError):
    code = "rate_limited"
    status_code = 429
    public_message = "Too many requests. Try again later."

    def __init__(self, retry_after: int) -> None:
        super().__init__()
        self.retry_after = retry_after


class RequestTimedOut(AppError):
    code = "timeout"
    status_code = 504
    public_message = "Discovery took too long. Try a more specific query."


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for noisy in ("httpx", "httpcore", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _error(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    body = ErrorResponse.model_validate({"error": ErrorDetail(code=code, message=message), **extra})
    return JSONResponse(status_code=status, content=body.model_dump(mode="json", exclude_none=True))


def _container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


async def enforce_access(request: Request) -> None:
    container = _container(request)
    token = container.settings.api_auth_token
    if token is not None and token.get_secret_value():
        supplied = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(supplied.encode(), token.get_secret_value().encode()):
            raise Unauthorized()
    client = request.client.host if request.client else "unknown"
    allowed, retry_after = container.rate_limiter.check(client)
    if not allowed:
        raise RateLimited(retry_after)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(settings)
        try:
            yield
        finally:
            await app.state.container.aclose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = (time.perf_counter() - started) * 1000
        path = request.url.path
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if not path.startswith(("/docs", "/redoc", "/openapi.json")):
            response.headers["Content-Security-Policy"] = _API_CSP
        logger.info(
            "%s %s -> %d in %.0f ms [%s]", request.method, path, response.status_code, elapsed, request_id
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        extra: dict[str, object] = {}
        if isinstance(exc, DiscoveryUnavailableError) and exc.platforms:
            extra["platforms"] = exc.platforms
        response = _error(exc.status_code, exc.code, exc.message, **extra)
        if isinstance(exc, RateLimited):
            response.headers["Retry-After"] = str(exc.retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = []
        for err in exc.errors()[:3]:
            location = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            problems.append(f"{location}: {err.get('msg')}" if location else str(err.get("msg")))
        return _error(422, "invalid_request", "; ".join(problems) or "Invalid request body.")

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error(exc.status_code, "http_error", message)

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s", request.url.path)
        return _error(500, "internal_error", "An unexpected error occurred.")

    @app.post(
        "/prospect-discovery",
        response_model=ProspectDiscoveryResponse,
        tags=["Discovery"],
        summary="Discover prospects from a natural-language query",
        responses={
            400: {"model": ErrorResponse, "description": "Query is not a prospect search"},
            401: {"model": ErrorResponse, "description": "Missing or wrong API key"},
            422: {"model": ErrorResponse, "description": "Malformed request body"},
            429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
            503: {"model": ErrorResponse, "description": "No discovery source available"},
            504: {"model": ErrorResponse, "description": "Discovery timed out"},
        },
        dependencies=[Depends(enforce_access)],
    )
    async def prospect_discovery(
        payload: ProspectDiscoveryRequest, request: Request
    ) -> ProspectDiscoveryResponse:
        service = _container(request).prospect_service
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                return await service.discover(payload.query, payload.max_results)
        except TimeoutError:
            raise RequestTimedOut() from None

    @app.get("/health", response_model=HealthResponse, tags=["Operations"])
    async def health(request: Request) -> HealthResponse:
        container = _container(request)
        return HealthResponse(
            version=__version__,
            search_provider=container.search_provider,
            llm_provider=f"{container.llm.provider}:{container.llm.model}" if container.llm else None,
            youtube_api=container.youtube_api,
        )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app


app = create_app()

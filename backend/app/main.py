from __future__ import annotations

import logging
import mimetypes
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .config import ConfigurationError, Settings
from .database import Database
from .instagram import InstagramAPIError, InstagramClient
from .media_checks import (
    check_one_product_media,
    run_scheduled_media_checks,
    serialize_media_check_result,
)
from .schemas import HealthResponse, ProductStatusUpdate
from .security import require_admin_key, verify_meta_signature
from .webhooks import extract_comment_events, process_comment_event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
SEOUL_TZ = ZoneInfo("Asia/Seoul")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings.db_path)
    instagram = InstagramClient(settings)
    scheduler = AsyncIOScheduler(timezone=SEOUL_TZ)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        scheduler.add_job(
            run_scheduled_media_checks,
            trigger=CronTrigger(
                hour=settings.media_check_hour,
                minute=0,
                timezone=SEOUL_TZ,
            ),
            kwargs={"database": database, "instagram": instagram},
            id="daily-instagram-media-check",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=60 * 60 * 3,
        )
        scheduler.start()
        logger.info(
            "jjabtree API 시작: db=%s uploads=%s media_check=%02d:00 KST",
            settings.db_path,
            settings.upload_dir,
            settings.media_check_hour,
        )
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            logger.info("jjabtree 스케줄러 종료")

    app = FastAPI(
        title="jjabtree API",
        version="0.1.0",
        description="Instagram 릴스 상품 링크페이지 및 댓글 기반 DM 자동화 API",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.instagram = instagram
    app.state.scheduler = scheduler

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

    admin_guard = require_admin_key(settings)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/public/products")
    async def public_products(request: Request):
        rows = database.list_public_products()
        for row in rows:
            row["photo_url"] = _absolute_photo_url(request, row["photo_url"])
        return {"products": rows}

    @app.get("/api/admin/media", dependencies=[Depends(admin_guard)])
    async def recent_media(limit: int = Query(default=24, ge=1, le=100)):
        try:
            media = await instagram.list_recent_media(limit=limit)
        except InstagramAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"media": media}

    @app.get("/api/admin/products", dependencies=[Depends(admin_guard)])
    async def admin_products(request: Request):
        rows = database.list_admin_products()
        for row in rows:
            row["photo_url"] = _absolute_photo_url(request, row["photo_url"])
        return {"products": rows}

    @app.get("/api/admin/dm-logs", dependencies=[Depends(admin_guard)])
    async def dm_logs(limit: int = Query(default=100, ge=1, le=500)):
        return {"logs": database.list_dm_logs(limit)}

    @app.post(
        "/api/admin/products",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(admin_guard)],
    )
    async def create_product(
        request: Request,
        product_name: str = Form(..., min_length=1, max_length=200),
        purchase_link: str = Form(..., min_length=1, max_length=2000),
        trigger_phrase: str = Form(..., min_length=1, max_length=100),
        ig_media_id: str = Form(..., min_length=1, max_length=200),
        ig_permalink: str = Form(..., min_length=1, max_length=2000),
        photo: UploadFile = File(...),
    ):
        _validate_http_url(purchase_link, "구매링크")
        _validate_http_url(ig_permalink, "Instagram permalink")
        photo_url = await _save_upload(photo, settings.upload_dir)
        try:
            product = database.create_product(
                {
                    "product_name": product_name.strip(),
                    "purchase_link": purchase_link.strip(),
                    "trigger_phrase": trigger_phrase.strip(),
                    "photo_url": photo_url,
                    "ig_media_id": ig_media_id.strip(),
                    "ig_permalink": ig_permalink.strip(),
                }
            )
        except Exception as exc:
            (settings.upload_dir / Path(photo_url).name).unlink(missing_ok=True)
            if "UNIQUE constraint failed: products.ig_media_id" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="이미 다른 상품에 연결된 Instagram 게시물입니다.",
                ) from exc
            raise

        subscription = await instagram.subscribe_comments()
        product["photo_url"] = _absolute_photo_url(request, product["photo_url"])
        return {
            "product": product,
            "webhook_subscription": {
                "ok": subscription.ok,
                "detail": subscription.detail,
            },
        }

    @app.patch(
        "/api/admin/products/{product_id}/status",
        dependencies=[Depends(admin_guard)],
    )
    async def set_product_status(
        product_id: int,
        payload: ProductStatusUpdate,
        request: Request,
    ):
        product = database.update_product_status(product_id, payload.status)
        if not product:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")
        product["photo_url"] = _absolute_photo_url(request, product["photo_url"])
        return {"product": product}

    @app.post(
        "/api/admin/products/{product_id}/media-check",
        dependencies=[Depends(admin_guard)],
    )
    async def check_product_media(product_id: int, request: Request):
        product = database.get_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")

        try:
            result, updated = await check_one_product_media(
                product,
                database=database,
                instagram=instagram,
            )
        except Exception as exc:  # noqa: BLE001 - manual check must not destabilize API
            logger.exception("수동 릴스 확인 실패: product_id=%s", product_id)
            raise HTTPException(
                status_code=502,
                detail="릴스 확인 중 예상하지 못한 오류가 발생했습니다. 잠시 후 다시 시도하세요.",
            ) from exc

        if not updated:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")
        updated["photo_url"] = _absolute_photo_url(request, updated["photo_url"])
        return {
            "check": serialize_media_check_result(result),
            "product": updated,
        }

    @app.delete(
        "/api/admin/products/{product_id}",
        dependencies=[Depends(admin_guard)],
    )
    async def delete_product(product_id: int):
        product = database.delete_product(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="상품을 찾을 수 없습니다.")

        photo_url = product.get("photo_url") or ""
        if photo_url.startswith("/uploads/"):
            try:
                (settings.upload_dir / Path(photo_url).name).unlink(missing_ok=True)
            except OSError:
                logger.exception("삭제 상품 이미지 정리 실패: product_id=%s", product_id)

        logger.info("상품 삭제 완료: product_id=%s", product_id)
        return {"deleted": True, "product_id": product_id}

    @app.get("/api/webhooks/instagram", response_class=PlainTextResponse)
    async def verify_webhook(
        hub_mode: str | None = Query(default=None, alias="hub.mode"),
        hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
        hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    ):
        if hub_mode == "subscribe" and hub_verify_token == settings.webhook_verify_token:
            return hub_challenge or ""
        raise HTTPException(status_code=403, detail="웹훅 검증 토큰이 일치하지 않습니다.")

    @app.post("/api/webhooks/instagram")
    async def receive_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_hub_signature_256: str | None = Header(default=None),
    ):
        raw = await request.body()
        if not verify_meta_signature(raw, x_hub_signature_256, settings.meta_app_secret):
            raise HTTPException(status_code=403, detail="Meta 웹훅 서명이 유효하지 않습니다.")
        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="JSON 웹훅 본문이 필요합니다.") from exc

        events = extract_comment_events(payload)
        for event in events:
            background_tasks.add_task(
                process_comment_event,
                event,
                database=database,
                instagram=instagram,
            )
        return {"received": True, "comment_events": len(events)}

    return app


async def _save_upload(photo: UploadFile, upload_dir: Path) -> str:
    content_type = (photo.content_type or "").lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=415,
            detail="JPG, PNG, WEBP, GIF 이미지만 업로드할 수 있습니다.",
        )

    data = await photo.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="이미지는 8MB 이하만 업로드할 수 있습니다.")
    if not data:
        raise HTTPException(status_code=400, detail="빈 이미지 파일은 업로드할 수 없습니다.")

    extension = (
        mimetypes.guess_extension(content_type)
        or Path(photo.filename or "").suffix
        or ".bin"
    )
    if extension == ".jpe":
        extension = ".jpg"
    filename = f"{uuid.uuid4().hex}{extension}"
    path = upload_dir / filename
    with path.open("wb") as output:
        output.write(data)
    return f"/uploads/{filename}"


def _validate_http_url(value: str, field_name: str) -> None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail=f"{field_name}는 http(s) URL이어야 합니다.")


def _absolute_photo_url(request: Request, value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return str(request.base_url).rstrip("/") + "/" + value.lstrip("/")


try:
    app = create_app()
except ConfigurationError:
    # Re-raise so Railway logs show the exact missing environment variable.
    raise
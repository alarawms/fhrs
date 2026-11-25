"""FastAPI app. Serves configuration (the active country lens), the stats snapshot
(N6 — no ad-hoc aggregates here), a minimal sample listing, and the static web app.
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from fhrs.core.config import Settings, get_settings
from fhrs.core.db import make_engine, make_session_factory
from fhrs.core.models import Sample, Visibility
from fhrs.stats.build import latest_snapshot


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    profile = settings.load_profile()
    web_dir = settings.resolved_web_dir()
    engine = make_engine(settings)
    sessions = make_session_factory(engine)

    app = FastAPI(title="FHRS", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "country": profile.code}

    @app.get("/config")
    def config() -> dict:
        return {
            "country_code": profile.code,
            "name": profile.name,
            "name_local": profile.name_local,
            "languages": profile.languages,
            "branding": profile.branding.model_dump(),
            "tiers": profile.footprint.tiers,
            "sources": profile.sources.insdc,
        }

    @app.get("/api/v1/stats")
    def stats() -> dict:
        with sessions() as session:
            snapshot = latest_snapshot(session, profile.code)
        if snapshot is None:
            raise HTTPException(
                status_code=503,
                detail="no stats snapshot yet — run `fhrs-stats build` after ingest",
            )
        return snapshot.payload

    @app.get("/api/v1/samples")
    def samples(
        limit: int = Query(default=20, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict:
        with sessions() as session:
            base = select(Sample).where(Sample.visibility == Visibility.public.value)
            total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
            rows = session.scalars(
                base.order_by(Sample.canonical_accession).limit(limit).offset(offset)
            ).all()
        return {
            "total": total,
            "hits": [
                {
                    "accession": s.canonical_accession,
                    "tier": s.tier,
                    "content_bucket": s.content_bucket,
                    "scientific_name": s.scientific_name,
                    "lat": s.lat,
                    "lon": s.lon,
                    "collection_date": s.collection_date,
                    "study_linked": s.study_id is not None,
                }
                for s in rows
            ],
        }

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def app_js() -> FileResponse:
        return FileResponse(web_dir / "app.js")

    return app


app = create_app()

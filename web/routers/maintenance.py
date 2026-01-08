"""Maintenance routes - cache audit and fix actions"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from web.config import TEMPLATES_DIR
from web.services.maintenance_service import get_maintenance_service
from web.services.web_cache import get_web_cache_service, CACHE_KEY_MAINTENANCE_AUDIT, CACHE_KEY_MAINTENANCE_HEALTH, CACHE_KEY_DASHBOARD_STATS

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# In-memory cache for full audit results (not JSON-serializable)
_audit_results_cache = {
    "results": None,
    "updated_at": None
}
_audit_cache_lock = __import__('threading').Lock()
AUDIT_CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_cache_age_display(key: str) -> Optional[str]:
    """Get human-readable cache age for a key"""
    web_cache = get_web_cache_service()
    _, updated_at = web_cache.get_with_age(key)
    if not updated_at:
        return None

    age_seconds = (datetime.now() - updated_at).total_seconds()
    if age_seconds < 60:
        return "just now"
    elif age_seconds < 3600:
        return f"{int(age_seconds / 60)} min ago"
    else:
        return f"{int(age_seconds / 3600)} hr ago"


def _invalidate_caches():
    """Invalidate all related caches after a maintenance action"""
    # Clear in-memory audit cache
    with _audit_cache_lock:
        _audit_results_cache["results"] = None
        _audit_results_cache["updated_at"] = None

    # Clear web caches
    web_cache = get_web_cache_service()
    web_cache.invalidate(CACHE_KEY_MAINTENANCE_AUDIT)
    web_cache.invalidate(CACHE_KEY_MAINTENANCE_HEALTH)
    web_cache.invalidate(CACHE_KEY_DASHBOARD_STATS)


def _get_cached_audit_results(force_refresh: bool = False):
    """Get audit results from cache or run fresh audit"""
    from datetime import datetime

    with _audit_cache_lock:
        now = datetime.now()

        # Check if cache is valid
        if not force_refresh and _audit_results_cache["results"] is not None:
            if _audit_results_cache["updated_at"]:
                age = (now - _audit_results_cache["updated_at"]).total_seconds()
                if age < AUDIT_CACHE_TTL_SECONDS:
                    return _audit_results_cache["results"], _audit_results_cache["updated_at"]

        # Run fresh audit
        service = get_maintenance_service()
        results = service.run_full_audit()

        # Update cache
        _audit_results_cache["results"] = results
        _audit_results_cache["updated_at"] = now

        # Also update the health summary in web cache
        web_cache = get_web_cache_service()
        web_cache.set(CACHE_KEY_MAINTENANCE_HEALTH, service.get_health_summary())

        return results, now


@router.get("/", response_class=HTMLResponse)
async def maintenance_page(request: Request):
    """Main maintenance page - loads instantly with skeleton, audit fetched via HTMX"""
    return templates.TemplateResponse(
        "maintenance/index.html",
        {
            "request": request,
            "page_title": "Maintenance"
        }
    )


@router.get("/audit", response_class=HTMLResponse)
async def run_audit(request: Request, refresh: bool = False):
    """Run audit and return HTMX partial with results"""
    results, updated_at = _get_cached_audit_results(force_refresh=refresh)

    # Calculate cache age display
    cache_age = None
    if updated_at:
        age_seconds = (datetime.now() - updated_at).total_seconds()
        if age_seconds < 60:
            cache_age = "just now"
        elif age_seconds < 3600:
            cache_age = f"{int(age_seconds / 60)} min ago"
        else:
            cache_age = f"{int(age_seconds / 3600)} hr ago"

    return templates.TemplateResponse(
        "maintenance/partials/audit_results.html",
        {
            "request": request,
            "results": results,
            "cache_age": cache_age or "just now"
        }
    )


@router.get("/health", response_class=HTMLResponse)
async def health_summary(request: Request):
    """Get health summary for dashboard widget"""
    service = get_maintenance_service()
    health = service.get_health_summary()

    return templates.TemplateResponse(
        "maintenance/partials/health_widget.html",
        {
            "request": request,
            "health": health
        }
    )


# === Action Routes ===

@router.post("/restore-plexcached", response_class=HTMLResponse)
async def restore_plexcached(
    request: Request,
    paths: List[str] = Form(default=[]),
    restore_all: bool = Form(default=False),
    dry_run: bool = Form(default=True)
):
    """Restore orphaned .plexcached backups"""
    service = get_maintenance_service()

    if restore_all:
        result = service.restore_all_plexcached(dry_run=dry_run)
    else:
        result = service.restore_plexcached(paths, dry_run=dry_run)

    # Invalidate caches if actual changes were made
    if not dry_run:
        _invalidate_caches()

    # Re-run audit to get updated results
    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/fix-with-backup", response_class=HTMLResponse)
async def fix_with_backup(
    request: Request,
    paths: List[str] = Form(default=[]),
    dry_run: bool = Form(default=True)
):
    """Fix files that have .plexcached backup"""
    service = get_maintenance_service()
    result = service.fix_with_backup(paths, dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/sync-to-array", response_class=HTMLResponse)
async def sync_to_array(
    request: Request,
    paths: List[str] = Form(default=[]),
    dry_run: bool = Form(default=True)
):
    """Move files to array - restores backups if they exist, copies if not"""
    service = get_maintenance_service()
    result = service.sync_to_array(paths, dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/add-to-exclude", response_class=HTMLResponse)
async def add_to_exclude(
    request: Request,
    paths: List[str] = Form(default=[]),
    dry_run: bool = Form(default=True)
):
    """Add files to exclude list"""
    service = get_maintenance_service()
    result = service.add_to_exclude(paths, dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/protect-with-backup", response_class=HTMLResponse)
async def protect_with_backup(
    request: Request,
    paths: List[str] = Form(default=[]),
    dry_run: bool = Form(default=True)
):
    """Protect files by creating .plexcached backup on array and adding to exclude list"""
    service = get_maintenance_service()
    result = service.protect_with_backup(paths, dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/clean-exclude", response_class=HTMLResponse)
async def clean_exclude(
    request: Request,
    dry_run: bool = Form(default=True)
):
    """Clean stale exclude entries"""
    service = get_maintenance_service()
    result = service.clean_exclude(dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/clean-timestamps", response_class=HTMLResponse)
async def clean_timestamps(
    request: Request,
    dry_run: bool = Form(default=True)
):
    """Clean stale timestamp entries"""
    service = get_maintenance_service()
    result = service.clean_timestamps(dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/fix-timestamps", response_class=HTMLResponse)
async def fix_timestamps(
    request: Request,
    paths: List[str] = Form(default=[]),
    dry_run: bool = Form(default=True)
):
    """Fix invalid file timestamps"""
    service = get_maintenance_service()
    result = service.fix_file_timestamps(paths, dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


@router.post("/resolve-duplicate", response_class=HTMLResponse)
async def resolve_duplicate(
    request: Request,
    cache_path: str = Form(...),
    keep: str = Form(...),  # "cache" or "array"
    dry_run: bool = Form(default=True)
):
    """Resolve a duplicate file"""
    service = get_maintenance_service()
    result = service.resolve_duplicate(cache_path, keep, dry_run=dry_run)

    if not dry_run:
        _invalidate_caches()

    audit_results = service.run_full_audit()

    return templates.TemplateResponse(
        "maintenance/partials/action_result.html",
        {
            "request": request,
            "action_result": result,
            "results": audit_results,
            "dry_run": dry_run
        }
    )


# === Preview Routes (always dry_run) ===

@router.get("/preview/restore-plexcached", response_class=HTMLResponse)
async def preview_restore_plexcached(request: Request):
    """Preview what restore-plexcached would do"""
    service = get_maintenance_service()
    result = service.restore_all_plexcached(dry_run=True)

    return templates.TemplateResponse(
        "maintenance/partials/preview_result.html",
        {
            "request": request,
            "action": "Restore .plexcached Backups",
            "result": result
        }
    )


@router.get("/preview/clean-exclude", response_class=HTMLResponse)
async def preview_clean_exclude(request: Request):
    """Preview what clean-exclude would do"""
    service = get_maintenance_service()
    result = service.clean_exclude(dry_run=True)
    stale_entries = list(service.get_exclude_files() - service.get_cache_files())[:50]

    return templates.TemplateResponse(
        "maintenance/partials/preview_result.html",
        {
            "request": request,
            "action": "Clean Stale Exclude Entries",
            "result": result,
            "items": stale_entries
        }
    )


@router.get("/preview/clean-timestamps", response_class=HTMLResponse)
async def preview_clean_timestamps(request: Request):
    """Preview what clean-timestamps would do"""
    service = get_maintenance_service()
    result = service.clean_timestamps(dry_run=True)
    stale_entries = list(service.get_timestamp_files() - service.get_cache_files())[:50]

    return templates.TemplateResponse(
        "maintenance/partials/preview_result.html",
        {
            "request": request,
            "action": "Clean Stale Timestamp Entries",
            "result": result,
            "items": stale_entries
        }
    )

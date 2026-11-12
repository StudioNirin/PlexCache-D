"""
System utilities for PlexCache.
Handles OS detection, system-specific operations, and path conversions.
"""

import os
import platform
import posixpath
import re
import shutil
import subprocess
import atexit
import fcntl
from typing import List, Tuple, Optional, NamedTuple, Callable, Set
import logging


# ============================================================================
# Disk Usage Types
# ============================================================================

class DiskUsage(NamedTuple):
    """Disk usage statistics compatible with shutil.disk_usage() return type."""
    total: int
    used: int
    free: int


# ============================================================================
# Unraid Disk Utilities
# ============================================================================

def resolve_user0_to_disk(user0_path: str) -> Optional[str]:
    """Resolve /mnt/user0/path to the actual /mnt/diskX/path on Unraid.

    On Unraid, /mnt/user0/ is a FUSE-based aggregate of all array disks.
    This function finds which physical disk a file actually lives on.

    Args:
        user0_path: A path starting with /mnt/user0/

    Returns:
        The actual /mnt/diskX/ path if found, None otherwise.
    """
    if not user0_path.startswith('/mnt/user0/'):
        return None

    relative_path = user0_path[len('/mnt/user0/'):]

    # Check each disk (Unraid supports up to 30 data disks)
    for disk_num in range(1, 31):
        disk_path = f'/mnt/disk{disk_num}/{relative_path}'
        if os.path.exists(disk_path):
            return disk_path

    return None


# ============================================================================
# Share Alignment Validation
# ============================================================================

# Unraid mount points that aggregate shares rather than being a share root
# themselves. Everything else under /mnt/ is treated as a pool name.
_SHARE_AGGREGATE_MOUNTS = ("user", "user0")


def extract_unraid_share(path: str) -> Optional[str]:
    """Return the Unraid share name a path belongs to, or None if undeterminable.

    Every Unraid path is `/mnt/<mount>/<share>/...` where `<mount>` is `user`,
    `user0`, `diskN`, or a pool name. The share name is the segment after it.

        /mnt/user/Movies/Action/       -> "Movies"
        /mnt/user0/data/media/movies/  -> "data"
        /mnt/disk3/data/media/         -> "data"
        /mnt/cache_downloads/Movies/   -> "Movies"

    Returns None for paths that aren't under /mnt/ or have no segment past the
    mount point, so callers can skip validation rather than guess.
    """
    if not path:
        return None
    normalized = posixpath.normpath(path.strip())
    if not normalized.startswith("/mnt/"):
        return None
    parts = [p for p in normalized[len("/mnt/"):].split("/") if p]
    if len(parts) < 2:
        return None  # bare mount point, no share segment
    return parts[1]


def check_cache_share_alignment(
    real_path: str,
    cache_path: str,
    host_cache_path: Optional[str] = None,
) -> Optional[dict]:
    """Check that a mapping's cache destination is the same Unraid share as its media.

    Unraid presents a share's pool tier and array tier merged under
    /mnt/user/<share>/. Caching only works when both tiers belong to the *same*
    share: a file moves between them and the /mnt/user/ path Plex reads never
    changes. Point the cache at a different share and the cached copy lands
    somewhere Plex isn't looking, leaving only the .plexcached stub behind
    (issues #189, #196).

    The host-side cache path is preferred when set, because under Docker
    `cache_path` is a container path whose prefix may be remapped.

    Args:
        real_path: Where the media lives (container path).
        cache_path: Where cached copies go (container path).
        host_cache_path: Host-side equivalent of cache_path, when Docker remaps it.

    Returns:
        None when the paths agree or can't be compared. Otherwise a dict with
        `kind` ("share_mismatch" or "cache_not_absolute"), the share names
        involved, and a human-readable `message`.
    """
    if not real_path or not cache_path:
        return None

    effective_cache = (host_cache_path or "").strip() or cache_path

    # Anchor on the media path. If it isn't an Unraid-style share path, this
    # install doesn't follow the layout these checks reason about — stay quiet
    # rather than guess. This is advisory and must not cry wolf.
    real_share = extract_unraid_share(real_path)
    if not real_share:
        return None

    # The media is on an Unraid share, so its cache tier has to live under /mnt/
    # too. A path like "/movies/" is a container-relative fragment: it resolves
    # to no share, and it also breaks the mover exclude file, which needs real
    # host paths.
    if not posixpath.normpath(effective_cache.strip()).startswith("/mnt/"):
        label = "Host Cache Path" if (host_cache_path or "").strip() else "Cache Path"
        return {
            "kind": "cache_not_under_mnt",
            "real_share": real_share,
            "cache_share": None,
            "message": (
                f"{label} '{effective_cache}' is not under /mnt/. Media is on the "
                f"'{real_share}' share, so the cache path should be that share's pool "
                f"tier, for example '/mnt/<pool>/{real_share}/...'."
            ),
        }

    cache_share = extract_unraid_share(effective_cache)
    if not cache_share:
        return None  # bare mount point, nothing to compare

    if real_share == cache_share:
        return None

    return {
        "kind": "share_mismatch",
        "real_share": real_share,
        "cache_share": cache_share,
        "message": (
            f"Media is in the '{real_share}' share but the cache path points into "
            f"'{cache_share}'. Cached files would land in a different share than Plex "
            f"reads from, so they would appear missing. The cache path should be the "
            f"pool tier of '{real_share}'."
        ),
    }


# ZFS-backed path prefixes that should NOT be converted to /mnt/user0/.
# For ZFS pool-only shares (shareUseCache=only), files never appear at /mnt/user0/
# because that path only shows standard array disks. Using /mnt/user/ is safe for
# these paths since there is no cache/array split — no FUSE ambiguity exists.
# Populated at startup by detect_zfs() checks on each path_mapping's real_path.
#
# NOTE: This is a performance hint — get_array_direct_path() uses it to avoid
# unnecessary /mnt/user0/ conversion for known pool-only shares. Safety-critical
# operations (_move_to_cache, _move_to_array) also probe /mnt/user0/ directly
# as defense in depth, so incorrect detection here won't cause data loss.
_zfs_user_prefixes: set = set()


def set_zfs_prefixes(prefixes: set) -> None:
    """Set the ZFS-backed path prefixes (called once at startup)."""
    global _zfs_user_prefixes
    _zfs_user_prefixes = prefixes


def get_array_direct_path(user_share_path: str) -> str:
    """Convert a user share path to array-direct path for existence checks.

    On Unraid, /mnt/user/ is a FUSE virtual filesystem that merges cache + array.
    When checking if a file exists ONLY on the array (not on cache), we need to
    use /mnt/user0/ which provides direct access to the array only.

    This is critical for eviction: we must verify a backup truly exists on the
    array before deleting the cache copy. Using /mnt/user/ would incorrectly
    return True if the file only exists on cache.

    Exception: ZFS pool-backed shares (shareUseCache=only) never have files at
    /mnt/user0/ — their files live on a ZFS pool, not array disks. For these
    paths, we skip the conversion and keep /mnt/user/ which is safe because
    there is no cache/array FUSE ambiguity.

    NOTE: This function uses _zfs_user_prefixes as a performance hint. Safety-
    critical callers (_move_to_cache, _move_to_array) also probe the filesystem
    directly as defense in depth.

    Args:
        user_share_path: A path potentially starting with /mnt/user/

    Returns:
        The /mnt/user0/ equivalent path if input is /mnt/user/ and not ZFS-backed,
        otherwise unchanged.
    """
    if user_share_path.startswith('/mnt/user/'):
        for prefix in _zfs_user_prefixes:
            if user_share_path.startswith(prefix):
                return user_share_path  # ZFS pool — no user0 conversion
        return '/mnt/user0/' + user_share_path[len('/mnt/user/'):]
    return user_share_path


def create_dir_with_ownership(
    path: str,
    src_file_for_permissions: Optional[str] = None,
    permissions: int = 0o777,
) -> None:
    """Create ``path`` (and any missing parents) with correct ownership/permissions.

    The container runs as root so it can move files of any ownership, but raw
    ``os.makedirs()`` then leaves new directories owned by ``root:root`` with a
    umask-derived mode (typically 0755). On Unraid that blocks Sonarr/Radarr from
    writing into newly created library folders. This helper chowns/chmods every
    newly created directory level so they match PUID/PGID (or the source file's
    owner when PUID/PGID are unset), the same way file copies are handled.

    Owner resolution: PUID/PGID environment variables take precedence; otherwise
    the owner of ``src_file_for_permissions`` is used (when it exists). chown/chmod
    are Linux-only and best-effort — failures (e.g. filesystem without ownership
    support) are logged at DEBUG and never raised. No-op if ``path`` already exists.

    Args:
        path: Directory path to create.
        src_file_for_permissions: File whose owner is used when PUID/PGID are unset.
        permissions: Mode applied to newly created directories (default 0o777).
    """
    if os.path.exists(path):
        return

    # Non-Linux (Windows dev/tests): just create the tree; no POSIX ownership.
    if not hasattr(os, "chown"):
        os.makedirs(path, exist_ok=True)
        return

    # Resolve target ownership: PUID/PGID env override, else source file's owner.
    target_uid: Optional[int] = None
    target_gid: Optional[int] = None
    for env_name, setter in (("PUID", "uid"), ("PGID", "gid")):
        env_val = os.environ.get(env_name)
        if env_val:
            try:
                if setter == "uid":
                    target_uid = int(env_val)
                else:
                    target_gid = int(env_val)
            except ValueError:
                pass  # Invalid env value — fall back to source ownership below.

    if (target_uid is None or target_gid is None) and src_file_for_permissions:
        try:
            stat_info = os.stat(src_file_for_permissions)
            if target_uid is None:
                target_uid = stat_info.st_uid
            if target_gid is None:
                target_gid = stat_info.st_gid
        except OSError:
            pass  # Source missing — leave any unresolved id as None (skip chown).

    # Track every directory level we are about to create so we can chown each one,
    # not just the leaf (os.makedirs only returns after creating the whole chain).
    dirs_to_create = []
    current = path
    while current and not os.path.exists(current):
        dirs_to_create.append(current)
        parent = os.path.dirname(current)
        if parent == current:  # Reached filesystem root.
            break
        current = parent
    dirs_to_create.reverse()  # Closest existing ancestor downward.

    original_umask = os.umask(0)
    try:
        os.makedirs(path, exist_ok=True)
        for dir_path in dirs_to_create:
            if target_uid is not None and target_gid is not None:
                try:
                    os.chown(dir_path, target_uid, target_gid)
                except (PermissionError, OSError) as e:
                    logging.debug(f"Could not set directory ownership for {dir_path}: {e}")
            try:
                os.chmod(dir_path, permissions)
            except (PermissionError, OSError) as e:
                logging.debug(f"Could not set directory permissions for {dir_path}: {e}")
    finally:
        os.umask(original_umask)


def parse_size_bytes(size_str: str) -> int:
    """Parse a human-readable size string and return bytes.

    Supports suffixes: TB/T, GB/G, MB/M. Bare numbers default to GB.
    Returns 0 for empty, zero, or invalid input.

    Args:
        size_str: Size string like "500GB", "1.5T", "100MB", or "2" (= 2GB).

    Returns:
        Size in bytes, or 0 if input is empty/zero/invalid.
    """
    if not size_str or size_str.strip() == "0":
        return 0
    size_str = size_str.strip().upper()
    units = (('TB', 1024**4), ('GB', 1024**3), ('MB', 1024**2),
             ('T', 1024**4), ('G', 1024**3), ('M', 1024**2))
    number, multiplier = size_str, 1024**3  # Bare numbers default to GB
    for suffix, factor in units:
        if size_str.endswith(suffix):
            number, multiplier = size_str[:-len(suffix)], factor
            break
    try:
        value = int(float(number) * multiplier)
    except ValueError:
        value = None
    # Callers treat a negative result as a percentage (see
    # ConfigManager._parse_cache_limit), so a negative size must never get through.
    if value is None or value < 0:
        # 0 means "no limit" to every caller, so an unparseable value silently
        # removes the cap the user thought they set. Say so; callers that can
        # name the setting add their own message on top.
        logging.warning(f"Could not read '{size_str}' as a size. Expected e.g. 500GB, 1.5T or 250. Treating as unset.")
        return 0
    return value


def size_setting_error(value: str, allow_percent: bool = True) -> Optional[str]:
    """Why a size setting (cache_limit, min_free_space, ...) can't be read, or None.

    Accepts the same formats as parse_size_bytes() and, when allow_percent,
    a percentage from just above 0 to 100. Empty or "0" means unset and is valid.
    Used by the Settings page to refuse a value the engine would ignore.
    """
    text = (value or "").strip()
    if not text or text == "0":
        return None
    hint = "Use a size like 500GB or 1.5T" + (", or a percentage like 75%" if allow_percent else "")
    if text.endswith("%"):
        if not allow_percent:
            return f"'{text}' is a percentage; this needs a size. {hint}."
        try:
            percent = float(text[:-1])
        except ValueError:
            return f"'{text}' isn't a percentage. {hint}."
        if not 0 < percent <= 100:
            return f"'{text}' must be more than 0% and at most 100%."
        return None
    if not re.fullmatch(r"\d+(\.\d+)?\s*(TB|GB|MB|T|G|M)?", text, re.IGNORECASE):
        return f"'{text}' isn't a size. {hint}."
    return None


def format_bytes(bytes_value: int) -> str:
    """Format bytes into human-readable string (e.g., '1.5 GB').

    This is the canonical implementation — use this everywhere instead of
    creating local _format_size / _format_bytes methods.

    Args:
        bytes_value: Size in bytes to format.

    Returns:
        Human-readable string with appropriate unit.
    """
    size = float(bytes_value)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024 or unit == 'TB':
            return f"{size:.2f} {unit}" if unit != 'B' else f"{int(size)} B"
        size /= 1024
    return f"{size:.2f} TB"


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration like '1m 23s' or '45s'.

    This is the canonical implementation — use this everywhere instead of
    creating local _format_duration methods.

    Args:
        seconds: Duration in seconds.

    Returns:
        Human-readable duration string.
    """
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins:02d}m"


def format_cache_age(updated_at) -> Optional[str]:
    """Format a datetime as a human-readable cache age string.

    Args:
        updated_at: datetime when the cache was last updated, or None.

    Returns:
        String like 'just now', '5 min ago', '2 hr ago', '3 days ago', or None
        if no timestamp.
    """
    if not updated_at:
        return None

    from datetime import datetime
    age_seconds = (datetime.now() - updated_at).total_seconds()
    if age_seconds < 60:
        return "just now"
    elif age_seconds < 3600:
        return f"{int(age_seconds / 60)} min ago"
    elif age_seconds < 86400:
        return f"{int(age_seconds / 3600)} hr ago"
    # Without a day tier the hour tier was terminal, so Recently Added's 30-day
    # window rendered "696 hr ago". int() truncation matches the tiers above.
    days = int(age_seconds / 86400)
    return f"{days} day ago" if days == 1 else f"{days} days ago"


def format_time_of_day(time_str: str, time_format: str) -> str:
    """Convert an 'HH:MM' string to display form based on time_format ('12h' or '24h').

    Invalid input is returned unchanged.
    """
    try:
        hour, minute = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return time_str
    if time_format == "12h":
        period = "AM" if hour < 12 else "PM"
        hour_12 = hour % 12 or 12
        if minute == 0:
            return f"{hour_12} {period}"
        return f"{hour_12}:{minute:02d} {period}"
    return f"{hour}:{minute:02d}"


def format_relative_time(target) -> str:
    """Format a future datetime as relative time (e.g., '12m', '2h 30m', '<1m').

    Returns 'now' if target is in the past. 'target' must be a datetime.
    """
    from datetime import datetime
    now = datetime.now()
    if target <= now:
        return "now"
    total_minutes = int((target - now).total_seconds()) // 60
    if total_minutes < 1:
        return "<1m"
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"


def get_log_time_datefmt(time_format: str) -> str:
    """Return a strftime datefmt for log timestamps based on time_format ('12h' or '24h')."""
    return '%-I:%M:%S %p' if time_format == '12h' else '%H:%M:%S'


def translate_container_to_host_path(path: str, path_mappings: list) -> str:
    """Translate container cache path to host path for exclude file.

    When Docker remaps cache paths, the exclude file needs host paths
    so the Unraid mover can understand them.

    Args:
        path: Container-side file path.
        path_mappings: List of path mapping dicts with 'host_cache_path' and 'cache_path'.

    Returns:
        Host-side path, or original path if no translation needed.
    """
    for mapping in path_mappings:
        host_cache_path = mapping.get('host_cache_path', '')
        cache_path = mapping.get('cache_path', '')

        if not host_cache_path or not cache_path:
            continue
        if host_cache_path == cache_path:
            continue  # No translation needed

        container_prefix = cache_path.rstrip('/')
        if path.startswith(container_prefix):
            host_prefix = host_cache_path.rstrip('/')
            return path.replace(container_prefix, host_prefix, 1)

    return path


def translate_host_to_container_path(path: str, path_mappings: list) -> str:
    """Translate host cache path to container path.

    When reading from the exclude file, paths are host paths but we need
    container paths to check file existence inside Docker.

    Args:
        path: Host-side file path.
        path_mappings: List of path mapping dicts with 'host_cache_path' and 'cache_path'.

    Returns:
        Container-side path, or original path if no translation needed.
    """
    for mapping in path_mappings:
        host_cache_path = mapping.get('host_cache_path', '')
        cache_path = mapping.get('cache_path', '')

        if not host_cache_path or not cache_path:
            continue
        if host_cache_path == cache_path:
            continue  # No translation needed

        host_prefix = host_cache_path.rstrip('/')
        if path.startswith(host_prefix):
            container_prefix = cache_path.rstrip('/')
            return path.replace(host_prefix, container_prefix, 1)

    return path


def remove_from_exclude_file(exclude_file_path, cache_path: str, path_mappings: list) -> None:
    """Remove a path from the Unraid mover exclude file.

    Args:
        exclude_file_path: Path to the exclude file (str or Path).
        cache_path: Container-side cache path to remove.
        path_mappings: Path mapping dicts for host/container translation.
    """
    from pathlib import Path
    exclude_file = Path(exclude_file_path) if not isinstance(exclude_file_path, Path) else exclude_file_path
    if not exclude_file.exists():
        return

    try:
        with open(exclude_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        host_path = translate_container_to_host_path(cache_path, path_mappings)
        new_lines = [line for line in lines if line.strip() != host_path]

        with open(exclude_file, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
    except IOError as e:
        logging.warning(f"Could not update exclude file: {e}")


def remove_from_timestamps_file(timestamps_file_path, cache_path: str) -> None:
    """Remove a path from the timestamps JSON file.

    Args:
        timestamps_file_path: Path to timestamps.json (str or Path).
        cache_path: Cache path key to remove.
    """
    import json
    from pathlib import Path
    ts_file = Path(timestamps_file_path) if not isinstance(timestamps_file_path, Path) else timestamps_file_path
    if not ts_file.exists():
        return

    try:
        with open(ts_file, 'r', encoding='utf-8') as f:
            timestamps = json.load(f)

        if cache_path in timestamps:
            del timestamps[cache_path]
            with open(ts_file, 'w', encoding='utf-8') as f:
                json.dump(timestamps, f, indent=2)
        else:
            logging.debug(f"Path not found in timestamps (may already be removed): {cache_path}")
    except (IOError, json.JSONDecodeError) as e:
        logging.warning(f"Could not update timestamps file: {e}")


def cleanup_empty_parent_folders(file_path: str, boundary_dir: str) -> int:
    """Remove folders left empty after a file was moved or deleted.

    This is the canonical implementation — use it everywhere instead of writing
    another rmdir walk. Implements the File and Folder Management Policy:
    PlexCache only removes folders it emptied itself, walking up from the
    removed file's parent and stopping at the first non-empty folder or at
    `boundary_dir`.

    Args:
        file_path: Path to the file that was just removed. It does not need to
            still exist — only its parent chain is inspected.
        boundary_dir: Directory to stop at (typically the cache root or the
            mapping's cache_path). Never removed, and nothing above it is
            touched. A `file_path` outside this boundary is a no-op.

    Returns:
        Number of folders removed.
    """
    if not file_path or not boundary_dir:
        return 0

    folders_removed = 0
    current_dir = os.path.dirname(file_path)
    boundary = os.path.normpath(boundary_dir)

    while current_dir:
        normalized_current = os.path.normpath(current_dir)

        # Never remove the boundary itself, and never climb above it. The
        # separator guard stops "/mnt/cache_downloads" from being treated as
        # living inside "/mnt/cache".
        if normalized_current == boundary:
            break
        if not normalized_current.startswith(boundary.rstrip(os.sep) + os.sep):
            break

        try:
            if not os.path.exists(current_dir):
                break

            if os.listdir(current_dir):
                logging.debug(f"Folder not empty, stopping cleanup: {current_dir}")
                break

            os.rmdir(current_dir)
            logging.debug(f"Removed empty folder (PlexCache cleanup): {current_dir}")
            folders_removed += 1

            current_dir = os.path.dirname(current_dir)

        except OSError as e:
            logging.debug(f"Could not remove folder {current_dir}: {type(e).__name__}: {e}")
            break

    return folders_removed


def sweep_empty_folders(cache_dirs: List[str],
                        should_skip_dir: Optional[Callable[[str], bool]] = None) -> int:
    """Remove every empty folder under `cache_dirs`.

    The canonical full sweep — use it instead of writing another os.walk/rmdir
    pass. Unlike `cleanup_empty_parent_folders()`, which walks up from one
    removed file, this scans whole trees. That makes it the tool for clearing a
    historical backlog, not for per-file cleanup during a run.

    The `cache_dirs` roots are never removed: os.walk only ever yields them as
    `root`, never inside a `dirs` list.

    Args:
        cache_dirs: Cache roots to scan (typically enabled mapping cache_paths).
        should_skip_dir: Optional predicate taking a bare directory *name*.
            Return True to leave it alone. Dot-directories (.Trash,
            .Recycle.Bin) are always skipped regardless.

    Returns:
        Number of folders removed.
    """
    removed = 0

    for cache_dir in cache_dirs or []:
        if not cache_dir or not os.path.exists(cache_dir):
            continue

        # topdown=False so children are visited before parents — a folder whose
        # only contents were empty folders becomes empty in the same pass.
        for root, dirs, _files in os.walk(cache_dir, topdown=False):
            for name in dirs:
                if name.startswith('.'):
                    continue
                if should_skip_dir and should_skip_dir(name):
                    continue

                dir_path = os.path.join(root, name)
                try:
                    if os.listdir(dir_path):
                        continue
                    os.rmdir(dir_path)
                    logging.debug(f"Removed empty folder (PlexCache sweep): {dir_path}")
                    removed += 1
                except OSError as e:
                    logging.debug(f"Could not remove folder {dir_path}: {type(e).__name__}: {e}")

    return removed


def resolve_cache_boundary(cache_path: str, path_mappings: list, fallback_dir: str = "") -> Optional[str]:
    """Find the cache root that `cache_path` lives under.

    Empty-folder cleanup must never climb past the cache root it started in, and
    multi-path setups can spread mappings across separate pools (`/mnt/cache`,
    `/mnt/ssd_cache`, ...), so a single global cache_dir is not a safe boundary
    for every file.

    Args:
        cache_path: The cache file whose boundary is being resolved.
        path_mappings: Settings path_mappings list (dicts with `cache_path`).
        fallback_dir: Legacy single cache_dir, used when no mapping matches.

    Returns:
        The longest matching enabled mapping cache_path, else `fallback_dir` if
        it contains the file, else None (caller should skip cleanup).
    """
    if not cache_path:
        return None

    normalized = os.path.normpath(cache_path)
    best = None

    for mapping in path_mappings or []:
        if not mapping.get('enabled', True):
            continue
        prefix = (mapping.get('cache_path') or '').rstrip('/\\')
        if not prefix:
            continue
        normalized_prefix = os.path.normpath(prefix)
        if normalized.startswith(normalized_prefix.rstrip(os.sep) + os.sep):
            # Longest match wins so nested mappings pick the deepest root.
            if best is None or len(normalized_prefix) > len(best):
                best = normalized_prefix

    if best:
        return best

    if fallback_dir:
        normalized_fallback = os.path.normpath(fallback_dir)
        if normalized.startswith(normalized_fallback.rstrip(os.sep) + os.sep):
            return normalized_fallback

    return None


def get_disk_free_space_bytes(path: str) -> int:
    """Get free space in bytes for the filesystem containing the given path.

    Args:
        path: Any path on the filesystem to check.

    Returns:
        Free space in bytes available for writing.
    """
    if not os.path.exists(path):
        # For files that don't exist yet, check the parent directory
        parent = os.path.dirname(path)
        if not os.path.exists(parent):
            return 0
        path = parent

    stat = os.statvfs(path)
    # f_bavail = blocks available to non-superuser (more accurate than f_bfree)
    return stat.f_bavail * stat.f_frsize


def get_disk_usage(path: str, total_override_bytes: int = 0) -> DiskUsage:
    """Get disk usage with optional manual total size override.

    On ZFS filesystems, statvfs() reports dataset-level stats which can be
    misleading (e.g., showing 1.7TB total when the pool is 3.7TB). Use the
    manual override (cache_drive_size setting) to specify correct pool capacity.

    When manual override is set, we keep the actual free space (which IS accurate
    on ZFS - it reflects pool free space) and calculate used from total - free.
    This gives correct results when mixing pool-level total with dataset stats.

    Args:
        path: Any path on the filesystem to check.
        total_override_bytes: Manual override for total capacity in bytes.
            If > 0, uses this value for total and calculates used from free.
            If 0, uses statvfs (may be inaccurate on ZFS).

    Returns:
        DiskUsage namedtuple with total, used, and free bytes.
    """
    usage = shutil.disk_usage(path)
    actual_total, actual_used, actual_free = usage.total, usage.used, usage.free

    # Apply manual override if provided
    if total_override_bytes > 0:
        # Keep actual_free (accurate on ZFS - reflects pool free space)
        # Calculate used as: manual_total - actual_free
        calculated_used = max(0, total_override_bytes - actual_free)
        return DiskUsage(total_override_bytes, calculated_used, actual_free)

    return DiskUsage(actual_total, actual_used, actual_free)


def detect_zfs(path: str) -> bool:
    """Detect if a path is on a ZFS filesystem.

    First tries df -T on the exact path. If that reports a non-ZFS type
    AND the path is under /mnt/user/ (Unraid FUSE), falls back to checking
    /proc/mounts for ZFS datasets mounted with the same share name.

    This fallback is needed because Unraid's FUSE layer (/mnt/user/) reports
    filesystem type as 'shfs' even when the underlying storage is ZFS.

    Args:
        path: Path to check.

    Returns:
        True if the path is on ZFS, False otherwise.
    """
    try:
        result = subprocess.run(
            ['df', '-T', path],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and 'zfs' in result.stdout.lower():
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fallback: For Unraid FUSE paths like /mnt/user/<share>/, df -T reports
    # 'shfs' instead of the underlying filesystem. Check /proc/mounts for
    # ZFS datasets with a mountpoint matching the share name.
    if path.startswith('/mnt/user/'):
        parts = path.rstrip('/').split('/')
        if len(parts) >= 4:
            share_name = parts[3]  # e.g., 'plex_media' from /mnt/user/plex_media/...
            return _check_zfs_mount_for_share(share_name)

    return False


def _check_zfs_mount_for_share(share_name: str) -> bool:
    """Check if a ZFS dataset is mounted with a matching share name.

    Reads /proc/mounts to find ZFS mounts where the mountpoint's last
    path component matches the Unraid share name. This detects ZFS-backed
    shares that are hidden behind Unraid's FUSE layer at /mnt/user/.

    Example /proc/mounts line:
        plex/plex_media /mnt/plex/plex_media zfs rw,xattr,posixacl ...

    Args:
        share_name: The Unraid share name (e.g., 'plex_media').

    Returns:
        True if a ZFS mount with a matching share name is found.
    """
    try:
        with open('/proc/mounts', 'r') as f:
            for line in f:
                fields = line.split()
                if len(fields) >= 3:
                    mountpoint = fields[1]
                    fs_type = fields[2]
                    if fs_type == 'zfs' and mountpoint.rstrip('/').endswith('/' + share_name):
                        logging.debug(f"ZFS mount detected via /proc/mounts: {mountpoint} (share: {share_name})")
                        return True
    except (OSError, IOError):
        pass
    return False


def get_disk_number_from_path(disk_path: str) -> Optional[str]:
    """Extract the disk number from a /mnt/diskX/ path.

    Args:
        disk_path: A path like /mnt/disk6/TV Shows/...

    Returns:
        The disk identifier (e.g., "disk6") or None if not a disk path.
    """
    if not disk_path.startswith('/mnt/disk'):
        return None

    # Extract "disk6" from "/mnt/disk6/TV Shows/..."
    parts = disk_path.split('/')
    if len(parts) >= 3 and parts[2].startswith('disk'):
        return parts[2]

    return None


class SingleInstanceLock:
    """
    Prevent multiple instances of PlexCache from running simultaneously.

    Uses flock to ensure only one instance can run at a time.
    The lock is automatically released when the process exits or crashes.
    """

    def __init__(self, lock_file: str):
        self.lock_file = lock_file
        self.lock_fd = None
        self.locked = False

    def acquire(self) -> bool:
        """
        Acquire the lock.

        Returns:
            True if lock acquired successfully, False if another instance is running.
        """
        try:
            self.lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write PID for debugging
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            self.locked = True

            # Register cleanup on exit
            atexit.register(self.release)

            return True

        except (IOError, OSError):
            # Lock is held by another process
            if self.lock_fd:
                self.lock_fd.close()
                self.lock_fd = None
            return False

    def release(self):
        """Release the lock and clean up."""
        if not self.locked:
            return

        try:
            if self.lock_fd:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
                self.lock_fd = None

            if os.path.exists(self.lock_file):
                os.remove(self.lock_file)

            self.locked = False
        except Exception:
            pass  # Best effort cleanup


class SystemDetector:
    """Detects and provides information about the current system."""
    
    def __init__(self):
        self.os_name = platform.system()
        self.is_linux = self.os_name != 'Windows'
        self.is_unraid = self._detect_unraid()
        self.is_docker = self._detect_docker()
        
    def _detect_unraid(self) -> bool:
        """Detect if running on Unraid system.

        Primary check: kernel version string contains 'Unraid' (e.g., '6.12.54-Unraid').
        Fallback: /mnt/user0/ exists (standard array systems).
        The kernel check works for all Unraid setups including ZFS-only pools
        where /mnt/user0/ doesn't exist.
        """
        if self.os_name != 'Linux':
            return False
        if 'unraid' in platform.release().lower():
            return True
        return os.path.exists('/mnt/user0/')
    
    def _detect_docker(self) -> bool:
        """Detect if running inside a Docker container."""
        return os.path.exists('/.dockerenv')

    def _parse_mountinfo(self) -> Set[str]:
        """Parse /proc/self/mountinfo and return the set of mount points.

        Cached for the process lifetime — mounts don't change without a
        container restart. Returns an empty set (permissive) if the file
        is unreadable.
        """
        if hasattr(self, '_mountinfo_cache'):
            return self._mountinfo_cache

        mount_points: Set[str] = set()
        try:
            with open('/proc/self/mountinfo', 'r') as f:
                for line in f:
                    # Format: id parent_id major:minor root mount_point options ...
                    # Field 5 (0-indexed: 4) is the mount point
                    parts = line.split()
                    if len(parts) >= 5:
                        mount_point = parts[4]
                        # Decode octal escapes (e.g., \040 for space)
                        mount_point = mount_point.encode('utf-8').decode('unicode_escape')
                        mount_points.add(mount_point)
        except (OSError, IOError):
            logging.warning(
                "Could not read /proc/self/mountinfo — Docker mount "
                "validation will be permissive (all paths accepted)"
            )

        self._mountinfo_cache = mount_points
        return mount_points

    def is_path_bind_mounted(self, path: str) -> Tuple[bool, Optional[str]]:
        """Check if a path falls under a real bind mount (not the overlay rootfs).

        Returns (True, owning_mount) when the path is safe to write to,
        or (False, None) when writes would go to docker.img.

        Non-Docker callers always get (True, None).
        """
        if not self.is_docker:
            return (True, None)

        mount_points = self._parse_mountinfo()
        if not mount_points:
            return (True, None)

        # Use posixpath since mountinfo is always Linux paths
        normalized = posixpath.normpath(path)
        best_match: Optional[str] = None
        best_len = 0

        for mp in mount_points:
            norm_mp = posixpath.normpath(mp)
            if normalized == norm_mp or normalized.startswith(norm_mp + '/'):
                if len(norm_mp) > best_len:
                    best_match = norm_mp
                    best_len = len(norm_mp)

        if best_match is None or best_match == '/':
            return (False, None)

        return (True, best_match)

    def validate_docker_mounts(self, paths: list) -> list:
        """Validate that paths are backed by real bind mounts in Docker.

        Uses /proc/self/mountinfo to definitively determine if paths fall
        under real bind mounts or the overlay rootfs (docker.img).

        Args:
            paths: List of paths to validate (e.g., ['/mnt/cache', '/mnt/user0'])

        Returns:
            List of warning messages for any issues found
        """
        warnings = []

        if not self.is_docker:
            return warnings

        for path in paths:
            if not path:
                continue

            path = path.rstrip('/')
            is_mounted, owning_mount = self.is_path_bind_mounted(path)

            if not is_mounted:
                warnings.append(
                    f"WARNING: {path} is not backed by a Docker bind mount — "
                    f"writes will go to the container's overlay filesystem "
                    f"(docker.img). Check your Docker volume configuration."
                )

        return warnings

class FileUtils:
    """Utility functions for file operations."""

    def __init__(self, is_linux: bool, permissions: int = 0o777, is_docker: bool = False):
        self.is_linux = is_linux
        self.permissions = permissions
        self.is_docker = is_docker

        # Check for PUID/PGID environment variables (Docker user/group override)
        self.puid = None
        self.pgid = None
        puid_env = os.environ.get('PUID')
        pgid_env = os.environ.get('PGID')

        if puid_env is not None:
            try:
                self.puid = int(puid_env)
            except ValueError:
                pass  # Will log warning when log_ownership_config() is called

        if pgid_env is not None:
            try:
                self.pgid = int(pgid_env)
            except ValueError:
                pass  # Will log warning when log_ownership_config() is called

    def log_ownership_config(self) -> None:
        """Log the file ownership configuration. Call after logging is set up."""
        puid_env = os.environ.get('PUID')
        pgid_env = os.environ.get('PGID')

        # Log any parse errors
        if puid_env is not None and self.puid is None:
            logging.warning(f"Invalid PUID value: {puid_env}, ignoring")
        if pgid_env is not None and self.pgid is None:
            logging.warning(f"Invalid PGID value: {pgid_env}, ignoring")

        # Log the ownership mode
        if self.puid is not None or self.pgid is not None:
            logging.info(f"File ownership: PUID={self.puid}, PGID={self.pgid}")
        elif self.is_docker:
            logging.info("File ownership: Using source file ownership (no PUID/PGID set)")
    
    def check_path_exists(self, path: str) -> None:
        """Check if path exists, is a directory, and is writable."""
        logging.debug(f"Checking path: {path}")
        
        if not os.path.exists(path):
            logging.error(f"Path does not exist: {path}")
            raise FileNotFoundError(f"Path {path} does not exist.")
        
        if not os.path.isdir(path):
            logging.error(f"Path is not a directory: {path}")
            raise NotADirectoryError(f"Path {path} is not a directory.")
        
        if not os.access(path, os.W_OK):
            logging.error(f"Path is not writable: {path}")
            raise PermissionError(f"Path {path} is not writable.")
        
        logging.debug(f"Path validation successful: {path}")
    
    def get_free_space(self, directory: str) -> Tuple[float, str]:
        """Get free space in a human-readable format."""
        if not os.path.exists(directory):
            raise FileNotFoundError(f"Invalid path, unable to calculate free space for: {directory}.")

        stat = os.statvfs(directory)
        free_space_bytes = stat.f_bfree * stat.f_frsize
        return self._convert_bytes_to_readable_size(free_space_bytes)

    def get_total_drive_size(self, directory: str) -> int:
        """Get total size of the drive in bytes."""
        if not os.path.exists(directory):
            raise FileNotFoundError(f"Invalid path, unable to calculate drive size for: {directory}.")

        stat = os.statvfs(directory)
        return stat.f_blocks * stat.f_frsize

    def get_total_size_of_files(self, files: list, warn_missing: bool = True) -> Tuple[float, str]:
        """Calculate total size of files in human-readable format.

        Args:
            files: Paths to size.
            warn_missing: Whether an unreadable path is worth a WARNING. Set
                False when the caller expects some paths to be absent. Sizing
                an array-destination move is the case that matters: PlexCache
                renamed those originals to .plexcached when it cached them, so
                every path legitimately misses and the warning is noise on an
                otherwise healthy restore. A genuinely missing file is still
                reported by _move_to_array, which checks cache and .plexcached
                before giving up.
        """
        total_size_bytes = 0
        skipped_files = []
        for file in files:
            try:
                total_size_bytes += os.path.getsize(file)
            except (OSError, FileNotFoundError):
                skipped_files.append(file)

        if skipped_files:
            file_word = "file" if len(skipped_files) == 1 else "files"
            if warn_missing:
                logging.warning(f"Skipping {len(skipped_files)} {file_word} not found on disk (may have been renamed - try refreshing Plex library)")
            else:
                logging.debug(f"Sizing skipped {len(skipped_files)} {file_word} not present at the requested path")
            for f in skipped_files:
                logging.debug(f"  Not found: {f}")

        return self._convert_bytes_to_readable_size(total_size_bytes)
    
    def _convert_bytes_to_readable_size(self, size_bytes: int) -> Tuple[float, str]:
        """Convert bytes to human-readable format."""
        if size_bytes >= (1024 ** 4):
            size = size_bytes / (1024 ** 4)
            unit = 'TB'
        elif size_bytes >= (1024 ** 3):
            size = size_bytes / (1024 ** 3)
            unit = 'GB'
        elif size_bytes >= (1024 ** 2):
            size = size_bytes / (1024 ** 2)
            unit = 'MB'
        else:
            size = size_bytes / 1024
            unit = 'KB'
        
        return size, unit
    
    def copy_file_with_permissions(
        self,
        src: str,
        dest: str,
        verbose: bool = False,
        display_src: str = None,
        display_dest: str = None,
        stop_check: Callable[[], bool] = None,
        chunk_size: int = 10 * 1024 * 1024,  # 10MB chunks for stop checks
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> int:
        """Copy a file preserving original ownership and permissions (Linux only).

        Args:
            src: Source file path
            dest: Destination file path
            verbose: If True, log detailed ownership info
            display_src: Optional path to show in logs instead of src (for Docker host paths)
            display_dest: Optional path to show in logs instead of dest (for Docker host paths)
            stop_check: Optional callback that returns True if copy should be cancelled.
                        Checked between chunks to allow mid-copy cancellation.
            chunk_size: Size of chunks for copy (default 10MB). Smaller = more responsive
                        to stop requests but slightly slower copy speed.
            progress_callback: Optional callback(bytes_copied, file_total) called after each chunk.

        If PUID/PGID environment variables are set, those values are used for ownership.
        Otherwise, the source file's ownership is preserved.

        Raises:
            InterruptedError: If stop_check returns True during copy (copy cancelled).
            RuntimeError: If copy fails for other reasons.
        """
        # Use display paths for logging if provided (Docker shows host paths)
        log_src = display_src or src
        log_dest = display_dest or dest
        logging.debug(f"Copying file from {log_src} to {log_dest}")

        try:
            if self.is_linux:
                # Get source file ownership and permissions before copy
                stat_info = os.stat(src)
                src_uid = stat_info.st_uid
                src_gid = stat_info.st_gid
                src_mode = stat_info.st_mode

                # Use PUID/PGID if set, otherwise use source ownership
                target_uid = self.puid if self.puid is not None else src_uid
                target_gid = self.pgid if self.pgid is not None else src_gid

                # Chunked copy with stop check and progress callback support
                # This allows cancelling mid-copy for large files
                file_size = stat_info.st_size
                bytes_copied = 0
                with open(src, 'rb') as fsrc:
                    with open(dest, 'wb') as fdest:
                        while True:
                            # Check for stop request between chunks
                            if stop_check and stop_check():
                                logging.debug(f"Copy cancelled by stop request: {log_dest}")
                                raise InterruptedError("Copy cancelled by user request")

                            chunk = fsrc.read(chunk_size)
                            if not chunk:
                                break
                            fdest.write(chunk)
                            bytes_copied += len(chunk)
                            if progress_callback:
                                progress_callback(bytes_copied, file_size)

                # Copy metadata (timestamps, etc.) - equivalent to what copy2 does
                shutil.copystat(src, dest)

                # Set ownership and permissions (shutil.copy2 doesn't preserve uid/gid)
                original_umask = os.umask(0)
                try:
                    os.chown(dest, target_uid, target_gid)
                except (PermissionError, OSError) as e:
                    logging.debug(f"Could not set file ownership (filesystem may not support it): {e}")

                try:
                    os.chmod(dest, src_mode)
                except (PermissionError, OSError) as e:
                    logging.debug(f"Could not set file permissions (filesystem may not support it): {e}")
                os.umask(original_umask)

                if verbose:
                    # Log ownership details for debugging
                    dest_stat = os.stat(dest)
                    logging.debug(f"File copied: {log_src} -> {log_dest}")
                    logging.debug(f"  Set ownership: uid={dest_stat.st_uid}, gid={dest_stat.st_gid}")
                    logging.debug(f"  Mode: {oct(dest_stat.st_mode)}")
                else:
                    logging.debug(f"File copied with permissions preserved: {log_dest}")
            else:  # Windows logic
                # Windows: use chunked copy for stop check or progress callback support
                if stop_check or progress_callback:
                    file_size = os.path.getsize(src)
                    bytes_copied = 0
                    with open(src, 'rb') as fsrc:
                        with open(dest, 'wb') as fdest:
                            while True:
                                if stop_check and stop_check():
                                    logging.debug(f"Copy cancelled by stop request: {log_dest}")
                                    raise InterruptedError("Copy cancelled by user request")
                                chunk = fsrc.read(chunk_size)
                                if not chunk:
                                    break
                                fdest.write(chunk)
                                bytes_copied += len(chunk)
                                if progress_callback:
                                    progress_callback(bytes_copied, file_size)
                    shutil.copystat(src, dest)
                else:
                    shutil.copy2(src, dest)
                logging.debug(f"File copied (Windows): {log_src} -> {log_dest}")

            return 0
        except InterruptedError:
            # Re-raise interruption so caller can handle cleanup
            raise
        except (FileNotFoundError, PermissionError, Exception) as e:
            logging.error(f"Error copying file from {log_src} to {log_dest}: {str(e)}")
            raise RuntimeError(f"Error copying file: {str(e)}")

    def create_directory_with_permissions(self, path: str, src_file_for_permissions: str) -> None:
        """Create directory with proper permissions.

        When creating multiple directory levels (e.g., Show/Season/), this ensures
        ALL newly created directories get the correct ownership, not just the final one.

        If PUID/PGID environment variables are set, those values are used for ownership.
        Otherwise, the source file's ownership is used.

        Thin instance wrapper around the module-level create_dir_with_ownership()
        so the same logic is shared with callers that have no FileUtils instance
        (e.g. the web service layer).
        """
        logging.debug(f"Creating directory with permissions: {path}")
        if os.path.exists(path):
            logging.debug(f"Directory already exists: {path}")
            return
        create_dir_with_ownership(path, src_file_for_permissions, permissions=self.permissions)
        logging.debug(f"Directory created with permissions: {path}")
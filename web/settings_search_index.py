"""Searchable Settings index.

Single source of truth for the Settings page search experience. Each entry maps
a user-facing setting to its tab + DOM target (data-setting-id) and carries the
metadata the frontend needs to render results: label, hint, section/subsection
breadcrumb, lucide icon, color tone, and keyword synonyms.

The index is exported to the client as JSON via a template global (see
web/config.py) and consumed by web/static/js/settings_search.js. Settings
templates annotate matching form-groups with data-setting-id="<id>" so the
click-to-jump flow can locate and flash the target field.

When adding a new setting in a template:
    1. Add data-setting-id="some_id" to the form-group div.
    2. Add a matching entry here.

This module lives at web/ (not under web/services/) to avoid a circular import:
web.config imports from here, but services/__init__.py imports cache_service
which re-imports web.config. Keeping this module as pure data with no project
imports lets web.config pull it in cleanly during initial load.

Tones map to CSS classes in custom.css:
    ""           -> default orange
    "tone-info"  -> blue
    "tone-ok"    -> green
    "tone-purple"-> purple
    "tone-warn"  -> amber/red
"""

from typing import Any, Dict, List

# Icon vocabulary aligns with lucide icons already used elsewhere in the UI.
# Tones map to the .result-icon.tone-* classes in custom.css.
_INDEX: List[Dict[str, Any]] = [
    # --- Plex Server -------------------------------------------------------
    {
        "tab": "plex", "setting_id": "plex_url",
        "label": "Plex URL", "hint": "Base URL of your Plex Media Server.",
        "section": "Plex Server", "subsection": "Server Connection",
        "icon": "link", "tone": "",
        "keywords": ["plex", "url", "host", "address", "server", "32400", "ip"],
    },
    {
        "tab": "plex", "setting_id": "plex_token",
        "label": "Plex Token",
        "hint": "Auth token. Click \"Get Token\" to sign in with Plex.",
        "section": "Plex Server", "subsection": "Server Connection",
        "icon": "key", "tone": "tone-warn",
        "keywords": ["plex", "token", "auth", "authentication", "login", "oauth", "sign in", "api key", "credentials"],
    },
    {
        "tab": "plex", "setting_id": "plex_oauth_btn",
        "label": "Get Token (Sign in with Plex)",
        "hint": "Open the Plex OAuth flow and capture a token automatically.",
        "section": "Plex Server", "subsection": "Server Connection",
        "icon": "key", "tone": "tone-warn",
        "keywords": ["oauth", "sign in", "plex login", "get token", "auto"],
    },
    {
        "tab": "plex", "setting_id": "plex_test",
        "label": "Test Plex Connection",
        "hint": "Verify the URL + token can reach your Plex server.",
        "section": "Plex Server", "subsection": "Server Connection",
        "icon": "plug", "tone": "tone-info",
        "keywords": ["test", "verify", "ping", "connection", "check"],
    },

    # --- Libraries --------------------------------------------------------
    {
        "tab": "libraries", "setting_id": "library_toggles",
        "label": "Enabled Libraries",
        "hint": "Pick which Plex libraries PlexCache should watch and cache from.",
        "section": "Libraries", "subsection": "",
        "icon": "library", "tone": "",
        "keywords": ["library", "libraries", "sections", "movies", "tv", "shows", "enable", "scan"],
    },
    {
        "tab": "libraries", "setting_id": "path_mappings",
        "label": "Path Mappings",
        "hint": "Translate Plex paths (e.g. /data/) into local mount paths (/mnt/user/) and define cache destinations.",
        "section": "Libraries", "subsection": "Custom Mappings",
        "icon": "folder", "tone": "tone-info",
        "keywords": ["path", "mapping", "mappings", "mount", "docker", "remap", "/mnt/", "/data/", "translate", "host cache", "plex path", "real path", "cache path", "name"],
    },

    # --- Users -------------------------------------------------------------
    {
        "tab": "users", "setting_id": "users_toggle",
        "label": "Enable Multi-User Support",
        "hint": "Cache media from other Plex Home users' OnDeck and Watchlist.",
        "section": "Users", "subsection": "Multi-User Support",
        "icon": "users", "tone": "",
        "keywords": ["multi-user", "users", "home users", "shared", "managed users", "enable"],
    },
    {
        "tab": "users", "setting_id": "users_sync_btn",
        "label": "Sync Users from Plex",
        "hint": "Refresh the list of Plex Home and shared users.",
        "section": "Users", "subsection": "User Preferences",
        "icon": "refresh-cw", "tone": "tone-info",
        "keywords": ["sync", "refresh", "users", "fetch", "reload"],
    },
    {
        "tab": "users", "setting_id": "auth_link_enabled",
        "label": "Enable Self-Service Auth Link",
        "hint": "Let shared users link their Plex account via a shareable URL so PlexCache can monitor their OnDeck. Requires PlexCache to be reachable (reverse proxy, Tailscale, etc.).",
        "section": "Users", "subsection": "Self-Service Authentication",
        "icon": "link", "tone": "tone-purple",
        "keywords": ["self-service", "auth link", "shareable", "invite", "share", "friends", "token capture"],
    },
    {
        "tab": "users", "setting_id": "auth_link_url",
        "label": "Shareable Auth Link",
        "hint": "URL to share with your Plex friends so they can self-link their account.",
        "section": "Users", "subsection": "Self-Service Authentication",
        "icon": "link", "tone": "tone-purple",
        "keywords": ["shareable", "url", "link", "invite", "share"],
    },
    {
        "tab": "users", "setting_id": "plex_db_path",
        "label": "Plex Database Path",
        "hint": "Path to the Plex database (read-only). Used to read OnDeck for shared users when no token is available.",
        "section": "Users", "subsection": "Database Fallback",
        "icon": "database", "tone": "tone-info",
        "keywords": ["plex", "database", "db", "sqlite", "fallback", "read-only", "ondeck"],
    },
    {
        "tab": "users", "setting_id": "remote_watchlist_toggle",
        "label": "Enable Remote Watchlist (RSS fallback) – Users",
        "hint": "Allow shared users to expose their watchlist via Plex RSS when the API isn't available.",
        "section": "Users", "subsection": "Remote User Watchlists",
        "icon": "rss", "tone": "tone-warn",
        "keywords": ["rss", "feed", "remote", "watchlist", "users", "fallback", "shared"],
    },
    {
        "tab": "users", "setting_id": "remote_watchlist_rss_url",
        "label": "Remote Watchlist RSS URL – Users",
        "hint": "Generate at: Plex Settings > Watchlist > Generate URL.",
        "section": "Users", "subsection": "Remote User Watchlists",
        "icon": "rss", "tone": "tone-warn",
        "keywords": ["rss", "feed", "url", "watchlist", "plex.tv", "remote"],
    },

    # --- Cache: Content Discovery -----------------------------------------
    {
        "tab": "cache", "setting_id": "number_episodes",
        "label": "Episodes to Prefetch",
        "hint": "Upcoming episodes to cache for in-progress shows.",
        "section": "Cache", "subsection": "Content Discovery",
        "icon": "database", "tone": "tone-ok",
        "keywords": ["episodes", "prefetch", "ondeck", "next", "tv", "shows", "count"],
    },
    {
        "tab": "cache", "setting_id": "days_to_monitor",
        "label": "Days to Monitor",
        "hint": "How far back to check OnDeck for recently watched items. A per-user override also sets that user's OnDeck retention threshold.",
        "section": "Cache", "subsection": "Content Discovery",
        "icon": "clock", "tone": "tone-ok",
        "keywords": ["days", "monitor", "ondeck", "window", "lookback", "history", "range"],
    },
    {
        "tab": "cache", "setting_id": "prefetch_minimum_minutes",
        "label": "Minimum Prefetch Runtime (minutes)",
        "hint": "Ensures prefetched episodes cover at least this many minutes of runtime. 0 disables.",
        "section": "Cache", "subsection": "Content Discovery",
        "icon": "clock", "tone": "tone-ok",
        "keywords": ["minimum", "runtime", "minutes", "prefetch", "duration", "episodes"],
    },
    {
        "tab": "cache", "setting_id": "recently_added_days",
        "label": "Recently Added — Default Window",
        "hint": "Default time window for the Recently Added page.",
        "section": "Cache", "subsection": "Content Discovery",
        "icon": "sparkles", "tone": "tone-ok",
        "keywords": ["recently", "added", "new", "window", "days", "default", "recent"],
    },
    {
        "tab": "cache", "setting_id": "recently_added_max_items",
        "label": "Recently Added — Max Items",
        "hint": "Most recent items to load on the Recently Added page (1–500).",
        "section": "Cache", "subsection": "Content Discovery",
        "icon": "sparkles", "tone": "tone-ok",
        "keywords": ["recently", "added", "new", "max", "items", "limit", "count"],
    },

    # --- Cache: Watchlist --------------------------------------------------
    {
        "tab": "cache", "setting_id": "watchlist_toggle",
        "label": "Enable Watchlist Caching",
        "hint": "Cache items from each user's Plex Watchlist.",
        "section": "Cache", "subsection": "Watchlist",
        "icon": "bookmark", "tone": "tone-ok",
        "keywords": ["watchlist", "queue", "enable", "toggle"],
    },
    {
        "tab": "cache", "setting_id": "watchlist_episodes",
        "label": "Watchlist Episodes to Prefetch",
        "hint": "Episodes to cache per watchlist item.",
        "section": "Cache", "subsection": "Watchlist",
        "icon": "bookmark", "tone": "tone-ok",
        "keywords": ["watchlist", "episodes", "prefetch", "count", "season"],
    },

    # --- Cache: Retention --------------------------------------------------
    {
        "tab": "cache", "setting_id": "cache_retention_hours",
        "label": "Cache Retention (hours)",
        "hint": "Keep files cached at least this long before moving back (0 = no minimum).",
        "section": "Cache", "subsection": "Retention",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["retention", "hours", "keep", "minimum", "cooldown"],
    },
    {
        "tab": "cache", "setting_id": "watchlist_retention_days",
        "label": "Watchlist Retention (days)",
        "hint": "Auto-expire watchlist items after this period (0 = never).",
        "section": "Cache", "subsection": "Retention",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["watchlist", "retention", "expire", "days", "cleanup"],
    },
    {
        "tab": "cache", "setting_id": "ondeck_retention_days",
        "label": "OnDeck Retention (days)",
        "hint": "Stop holding an item on cache once every user who has it OnDeck has had it this long (0 = never). Moves it back later, does not delete it.",
        "section": "Cache", "subsection": "Retention",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["ondeck", "retention", "expire", "days", "cleanup"],
    },

    # --- Cache: File Handling ---------------------------------------------
    {
        "tab": "cache", "setting_id": "watched_move",
        "label": "Move Watched Files Back to Array",
        "hint": "When a user finishes an item, move it from cache back to the array.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "arrow-left-right", "tone": "",
        "keywords": ["watched", "move", "array", "return", "restore"],
    },
    {
        "tab": "cache", "setting_id": "create_plexcached_backups",
        "label": "Create .plexcached Backup Files",
        "hint": "Keep a renamed copy on the array (.plexcached) when caching so files can be recovered if the cache drive fails.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "archive", "tone": "tone-info",
        "keywords": ["plexcached", "backup", "safety", "recovery", "array"],
    },
    {
        "tab": "cache", "setting_id": "cleanup_empty_folders",
        "label": "Clean Up Empty Folders",
        "hint": "Remove empty parent folders on cache after moving files to array.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "folder", "tone": "",
        "keywords": ["cleanup", "empty", "folders", "directories", "remove"],
    },
    {
        "tab": "cache", "setting_id": "use_symlinks",
        "label": "Create Symlinks After Caching",
        "hint": "Non-Unraid only: create a symlink at the original path pointing to the cached copy so Plex can find files.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "link", "tone": "tone-info",
        "keywords": ["symlink", "softlink", "link", "mergerfs", "non-unraid"],
    },
    {
        "tab": "cache", "setting_id": "auto_transfer_upgrades",
        "label": "Auto-Transfer Tracking on Media Upgrades",
        "hint": "When Sonarr/Radarr upgrades a cached file, transfer exclude list + tracker entries to the new file using Plex rating keys.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "trending-up", "tone": "tone-info",
        "keywords": ["upgrade", "sonarr", "radarr", "transfer", "tracking", "rating key", "quality"],
    },
    {
        "tab": "cache", "setting_id": "backup_upgraded_files",
        "label": "Backup Upgraded Files",
        "hint": "Create a .plexcached backup of the new file when a cached file is upgraded (only if the old file had a backup).",
        "section": "Cache", "subsection": "File Handling",
        "icon": "archive", "tone": "tone-info",
        "keywords": ["backup", "upgrade", "sonarr", "radarr", "plexcached"],
    },
    {
        "tab": "cache", "setting_id": "hardlinked_files",
        "label": "Files you are seeding",
        "hint": "Files shared with your torrent client. Leave them on the array until seeding finishes, or cache them anyway.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "link", "tone": "tone-warn",
        "keywords": ["hardlink", "hardlinks", "torrent", "seeding", "links"],
    },
    {
        "tab": "cache", "setting_id": "check_hardlinks_on_restore",
        "label": "Check Hardlinks on Restore",
        "hint": "Skip cached files with multiple hardlinks (e.g., actively seeding) when moving back to array.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "link", "tone": "tone-warn",
        "keywords": ["hardlink", "restore", "skip", "seeding", "torrent"],
    },
    {
        "tab": "cache", "setting_id": "cache_associated_files",
        "label": "Associated Files",
        "hint": "Which files to cache alongside videos: subtitles, artwork, NFO sidecars.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "file-text", "tone": "",
        "keywords": ["associated", "sidecar", "subtitles", "subs", "srt", "nfo", "artwork", "metadata"],
    },
    {
        "tab": "cache", "setting_id": "excluded_folders",
        "label": "Excluded Folders",
        "hint": "Folders to skip during scanning. Common examples: @Recycle, #recycle.",
        "section": "Cache", "subsection": "File Handling",
        "icon": "folder-x", "tone": "tone-warn",
        "keywords": ["excluded", "ignore", "skip", "folders", "recycle", "trash"],
    },

    # --- Cache: Storage Limits --------------------------------------------
    {
        "tab": "cache", "setting_id": "cache_drive_size",
        "label": "Cache Drive Size",
        "hint": "Override auto-detected size (e.g., 3.7TB). For ZFS pools where dataset size differs from pool size.",
        "section": "Cache", "subsection": "Storage Limits",
        "icon": "hard-drive", "tone": "",
        "keywords": ["cache", "drive", "size", "capacity", "zfs", "override"],
    },
    {
        "tab": "cache", "setting_id": "cache_limit",
        "label": "Cache Limit",
        "hint": "Max total drive usage before PlexCache stops caching (e.g., 500GB or 75%).",
        "section": "Cache", "subsection": "Storage Limits",
        "icon": "hard-drive", "tone": "tone-warn",
        "keywords": ["limit", "cap", "size", "max", "storage", "quota"],
    },
    {
        "tab": "cache", "setting_id": "min_free_space",
        "label": "Min Free Space",
        "hint": "Safety floor: always keep at least this much free space on the drive.",
        "section": "Cache", "subsection": "Storage Limits",
        "icon": "hard-drive", "tone": "tone-warn",
        "keywords": ["free", "space", "floor", "minimum", "safety", "reserve"],
    },
    {
        "tab": "cache", "setting_id": "plexcache_quota",
        "label": "PlexCache Quota",
        "hint": "Max space for PlexCache-managed files only (excludes other apps).",
        "section": "Cache", "subsection": "Storage Limits",
        "icon": "hard-drive", "tone": "tone-warn",
        "keywords": ["quota", "limit", "plexcache", "managed", "cap"],
    },

    # --- Cache: Eviction ---------------------------------------------------
    {
        "tab": "cache", "setting_id": "cache_eviction_mode",
        "label": "Eviction Mode",
        "hint": "How to choose files to evict when the cache fills.",
        "section": "Cache", "subsection": "Eviction",
        "icon": "trash-2", "tone": "tone-warn",
        "keywords": ["eviction", "evict", "mode", "policy", "lru", "priority"],
    },
    {
        "tab": "cache", "setting_id": "cache_eviction_threshold_percent",
        "label": "Eviction Threshold (%)",
        "hint": "Start evicting low-priority files when usage exceeds this percent of your Cache Limit.",
        "section": "Cache", "subsection": "Eviction",
        "icon": "trash-2", "tone": "tone-warn",
        "keywords": ["eviction", "threshold", "percent", "trigger", "watermark"],
    },
    {
        "tab": "cache", "setting_id": "eviction_min_priority",
        "label": "Minimum Priority to Keep",
        "hint": "Only evict files below this priority (OnDeck ~60-80, Watchlist ~40-60).",
        "section": "Cache", "subsection": "Eviction",
        "icon": "trash-2", "tone": "tone-warn",
        "keywords": ["eviction", "priority", "minimum", "keep", "protect", "score"],
    },

    # --- Cache: Advanced ---------------------------------------------------
    {
        "tab": "cache", "setting_id": "exit_if_active_session",
        "label": "Skip Run if Plex Has Active Playback",
        "hint": "Exit without processing if someone is currently watching.",
        "section": "Cache", "subsection": "Advanced",
        "icon": "pause", "tone": "tone-info",
        "keywords": ["active", "session", "playing", "watching", "skip", "exit", "playback"],
    },
    {
        "tab": "cache", "setting_id": "max_concurrent_moves_cache",
        "label": "Concurrent Moves to Cache",
        "hint": "Max parallel file operations when moving TO cache (default: 5).",
        "section": "Cache", "subsection": "Advanced",
        "icon": "git-branch", "tone": "",
        "keywords": ["concurrent", "parallel", "moves", "threads", "workers", "cache"],
    },
    {
        "tab": "cache", "setting_id": "max_concurrent_moves_array",
        "label": "Concurrent Moves to Array",
        "hint": "Max parallel file operations when moving TO array (default: 2).",
        "section": "Cache", "subsection": "Advanced",
        "icon": "git-branch", "tone": "",
        "keywords": ["concurrent", "parallel", "moves", "threads", "workers", "array"],
    },

    # --- Cache: Pinned Media ----------------------------------------------
    {
        "tab": "cache", "setting_id": "pinned_preferred_resolution",
        "label": "Pinned Preferred Version",
        "hint": "Which version to cache when a pinned item has multiple media files (e.g., 4K + 1080p).",
        "section": "Cache", "subsection": "Pinned Media",
        "icon": "pin", "tone": "tone-purple",
        "keywords": ["pinned", "pin", "preferred", "version", "resolution", "4k", "1080p"],
    },
    {
        "tab": "cache", "setting_id": "pinned_search",
        "label": "Search Plex (Pin Media)",
        "hint": "Pick a movie, show, season, or episode to pin to the cache.",
        "section": "Cache", "subsection": "Pinned Media",
        "icon": "search", "tone": "tone-purple",
        "keywords": ["pin", "pinned", "search", "plex", "media", "force cache"],
    },

    # --- Schedule ----------------------------------------------------------
    {
        "tab": "schedule", "setting_id": "schedule_enabled",
        "label": "Enable Scheduled Runs",
        "hint": "Run PlexCache automatically on a schedule while the Web UI is up.",
        "section": "Schedule", "subsection": "",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["schedule", "enable", "auto", "automatic", "cron", "timer"],
    },
    {
        "tab": "schedule", "setting_id": "schedule_type",
        "label": "Schedule Type",
        "hint": "Choose between simple interval or advanced cron expression.",
        "section": "Schedule", "subsection": "",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["schedule", "type", "interval", "cron", "mode"],
    },
    {
        "tab": "schedule", "setting_id": "interval_hours",
        "label": "Run Every (interval)",
        "hint": "Run interval in hours.",
        "section": "Schedule", "subsection": "",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["interval", "every", "hours", "frequency", "schedule"],
    },
    {
        "tab": "schedule", "setting_id": "interval_start_time",
        "label": "Starting At (anchor time)",
        "hint": "Anchor time for the schedule (e.g., 02:00 means runs at 02:00, 06:00, 10:00...).",
        "section": "Schedule", "subsection": "",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["start", "anchor", "time", "begin", "schedule"],
    },
    {
        "tab": "schedule", "setting_id": "cron_expression",
        "label": "Cron Expression",
        "hint": "Format: minute hour day month weekday. e.g., 0 */4 * * * (every 4 hours).",
        "section": "Schedule", "subsection": "",
        "icon": "clock", "tone": "tone-info",
        "keywords": ["cron", "crontab", "expression", "advanced", "schedule", "syntax"],
    },
    {
        "tab": "schedule", "setting_id": "cron_validate_btn",
        "label": "Validate Cron Expression",
        "hint": "Check that the cron syntax parses correctly.",
        "section": "Schedule", "subsection": "",
        "icon": "check", "tone": "tone-ok",
        "keywords": ["validate", "check", "cron", "syntax", "test"],
    },
    {
        "tab": "schedule", "setting_id": "dry_run",
        "label": "Dry Run",
        "hint": "Simulate without moving files (logs only).",
        "section": "Schedule", "subsection": "Run Options",
        "icon": "play-circle", "tone": "",
        "keywords": ["dry run", "dry-run", "simulate", "preview", "no-op"],
    },
    {
        "tab": "schedule", "setting_id": "verbose",
        "label": "Verbose Logging",
        "hint": "Enable detailed debug output during scheduled runs.",
        "section": "Schedule", "subsection": "Run Options",
        "icon": "file-text", "tone": "",
        "keywords": ["verbose", "debug", "logging", "detailed", "log"],
    },

    # --- Notifications -----------------------------------------------------
    {
        "tab": "notifications", "setting_id": "notification_type",
        "label": "Notification Type",
        "hint": "Choose Unraid, Webhook, or both.",
        "section": "Notifications", "subsection": "",
        "icon": "bell", "tone": "",
        "keywords": ["notify", "notification", "type", "channel"],
    },
    {
        "tab": "notifications", "setting_id": "unraid_levels",
        "label": "Unraid Notification Levels",
        "hint": "Which events to send to Unraid's notification system: summary, activity, success, warning, error.",
        "section": "Notifications", "subsection": "Unraid Notifications",
        "icon": "bell", "tone": "tone-info",
        "keywords": ["unraid", "levels", "summary", "activity", "success", "warning", "error"],
    },
    {
        "tab": "notifications", "setting_id": "webhook_url",
        "label": "Webhook URL",
        "hint": "Discord, Slack, or any Discord-compatible webhook endpoint.",
        "section": "Notifications", "subsection": "Webhook Notifications",
        "icon": "webhook", "tone": "tone-purple",
        "keywords": ["webhook", "discord", "slack", "url", "notify", "chat", "endpoint"],
    },
    {
        "tab": "notifications", "setting_id": "test_webhook_btn",
        "label": "Test Webhook",
        "hint": "Send a test notification to verify the webhook URL works.",
        "section": "Notifications", "subsection": "Webhook Notifications",
        "icon": "send", "tone": "tone-purple",
        "keywords": ["test", "webhook", "verify", "ping", "discord", "slack"],
    },
    {
        "tab": "notifications", "setting_id": "webhook_levels",
        "label": "Webhook Notification Levels",
        "hint": "Which events to send via webhook: summary, activity, success, warning, error.",
        "section": "Notifications", "subsection": "Webhook Notifications",
        "icon": "webhook", "tone": "tone-purple",
        "keywords": ["webhook", "levels", "summary", "activity", "success", "warning", "error"],
    },

    # --- Logging -----------------------------------------------------------
    {
        "tab": "logging", "setting_id": "max_log_files",
        "label": "Maximum Log Files",
        "hint": "Number of log files to keep (one per run).",
        "section": "Logging", "subsection": "Log Retention",
        "icon": "archive", "tone": "",
        "keywords": ["log", "logs", "retention", "rotation", "max", "files"],
    },
    {
        "tab": "logging", "setting_id": "keep_error_logs_days",
        "label": "Error Log Retention (days)",
        "hint": "Days to keep logs containing errors (0 = disabled).",
        "section": "Logging", "subsection": "Log Retention",
        "icon": "archive", "tone": "tone-warn",
        "keywords": ["error", "warning", "retention", "days", "keep"],
    },
    {
        "tab": "logging", "setting_id": "time_format",
        "label": "Time Format",
        "hint": "12-hour or 24-hour clock for timestamps in the Web UI.",
        "section": "Logging", "subsection": "Time Display",
        "icon": "clock", "tone": "",
        "keywords": ["time", "format", "12h", "24h", "am", "pm", "clock", "timestamp"],
    },
    {
        "tab": "logging", "setting_id": "activity_retention_hours",
        "label": "Recent Activity Retention (hours)",
        "hint": "How long Recent Activity entries persist on the Dashboard.",
        "section": "Logging", "subsection": "Dashboard",
        "icon": "activity", "tone": "tone-purple",
        "keywords": ["activity", "retention", "dashboard", "history", "recent", "hours"],
    },

    # --- Integrations ------------------------------------------------------
    {
        "tab": "integrations", "setting_id": "arr_instances",
        "label": "Sonarr / Radarr Instances",
        "hint": "Add a Sonarr or Radarr server. Used to detect quality-upgrade file swaps so trackers transfer correctly.",
        "section": "Integrations", "subsection": "",
        "icon": "plug", "tone": "tone-info",
        "keywords": ["sonarr", "radarr", "arr", "upgrades", "api", "name", "type", "url"],
    },
    {
        "tab": "integrations", "setting_id": "arr_api_key",
        "label": "Sonarr / Radarr API Key",
        "hint": "Found under Settings > General > API Key in Sonarr/Radarr.",
        "section": "Integrations", "subsection": "",
        "icon": "key", "tone": "tone-warn",
        "keywords": ["api key", "sonarr", "radarr", "arr", "token", "credentials"],
    },
    {
        "tab": "integrations", "setting_id": "arr_test_btn",
        "label": "Test Sonarr / Radarr Instance",
        "hint": "Verify the URL + API key can reach the instance.",
        "section": "Integrations", "subsection": "",
        "icon": "plug", "tone": "tone-info",
        "keywords": ["test", "sonarr", "radarr", "verify", "ping"],
    },

    # --- Security ----------------------------------------------------------
    {
        "tab": "security", "setting_id": "auth_enabled",
        "label": "Require Login for Web UI",
        "hint": "Only the Plex server owner can sign in to access the Web UI.",
        "section": "Security", "subsection": "Web UI Authentication",
        "icon": "shield", "tone": "tone-warn",
        "keywords": ["auth", "login", "password", "security", "gate", "sign in", "require"],
    },
    {
        "tab": "security", "setting_id": "auth_session_hours",
        "label": "Session Duration",
        "hint": "How long a login session lasts. \"Remember me\" extends to 7 days.",
        "section": "Security", "subsection": "Web UI Authentication",
        "icon": "clock", "tone": "tone-warn",
        "keywords": ["session", "duration", "expiry", "logout", "remember me"],
    },
    {
        "tab": "security", "setting_id": "auth_password_enabled",
        "label": "Enable Password Login",
        "hint": "Optional password login as fallback when Plex OAuth is unavailable.",
        "section": "Security", "subsection": "Password Fallback",
        "icon": "lock", "tone": "tone-warn",
        "keywords": ["password", "login", "fallback", "local", "credentials"],
    },
    {
        "tab": "security", "setting_id": "auth_password_username",
        "label": "Username (Password Fallback)",
        "hint": "Local username for password login.",
        "section": "Security", "subsection": "Password Fallback",
        "icon": "user", "tone": "tone-warn",
        "keywords": ["username", "login", "user", "credentials", "password"],
    },
    {
        "tab": "security", "setting_id": "auth_password",
        "label": "New Password",
        "hint": "Local password for fallback login. Leave blank to keep current.",
        "section": "Security", "subsection": "Password Fallback",
        "icon": "key", "tone": "tone-warn",
        "keywords": ["password", "auth", "login", "credentials", "change"],
    },
    {
        "tab": "security", "setting_id": "logout_all_btn",
        "label": "Sign Out All Sessions",
        "hint": "Invalidate every active login token.",
        "section": "Security", "subsection": "Active Sessions",
        "icon": "log-out", "tone": "tone-warn",
        "keywords": ["logout", "sign out", "sessions", "revoke", "all"],
    },
    {
        "tab": "security", "setting_id": "api_key",
        "label": "API Key",
        "hint": "Lets Tautulli, Home Assistant or a script start, check or stop a cache run.",
        "section": "Security", "subsection": "API Access",
        "icon": "key-round", "tone": "tone-warn",
        "keywords": ["api", "key", "token", "tautulli", "home assistant", "webhook", "automation", "trigger", "curl"],
    },
    {
        "tab": "security", "setting_id": "api_run_cooldown_seconds",
        "label": "API Run Cooldown",
        "hint": "Minimum gap between API-started runs; extra calls are merged into one follow-up run.",
        "section": "Security", "subsection": "API Access",
        "icon": "timer", "tone": "tone-warn",
        "keywords": ["api", "cooldown", "debounce", "rate", "limit", "tautulli", "trigger", "seconds"],
    },

    # --- Import / Export ---------------------------------------------------
    {
        "tab": "import-export", "setting_id": "include_sensitive",
        "label": "Include Sensitive Data in Export",
        "hint": "When unchecked, redacts tokens, URLs, webhooks, client ID, usernames, and user IDs.",
        "section": "Import / Export", "subsection": "Export Settings",
        "icon": "shield", "tone": "tone-warn",
        "keywords": ["sensitive", "redact", "export", "share", "tokens", "privacy"],
    },
    {
        "tab": "import-export", "setting_id": "download_backup_btn",
        "label": "Download Settings Backup",
        "hint": "Download your current settings as a JSON file for backup or migration.",
        "section": "Import / Export", "subsection": "Export Settings",
        "icon": "download", "tone": "",
        "keywords": ["download", "backup", "export", "save", "json", "settings"],
    },
    {
        "tab": "import-export", "setting_id": "settings_file",
        "label": "Settings File (Import)",
        "hint": "Select a PlexCache backup JSON file to import.",
        "section": "Import / Export", "subsection": "Import Settings",
        "icon": "upload", "tone": "",
        "keywords": ["import", "settings", "file", "json", "restore", "upload"],
    },
    {
        "tab": "import-export", "setting_id": "import_mode",
        "label": "Import Mode",
        "hint": "Merge with current settings or replace everything.",
        "section": "Import / Export", "subsection": "Import Settings",
        "icon": "arrow-left-right", "tone": "",
        "keywords": ["import", "mode", "merge", "replace", "overwrite"],
    },
    {
        "tab": "import-export", "setting_id": "validate_settings_btn",
        "label": "Validate Settings File",
        "hint": "Check the uploaded JSON before importing.",
        "section": "Import / Export", "subsection": "Import Settings",
        "icon": "check", "tone": "tone-ok",
        "keywords": ["validate", "check", "import", "settings", "verify"],
    },
    {
        "tab": "import-export", "setting_id": "import_settings_btn",
        "label": "Import Settings",
        "hint": "Apply the validated settings file.",
        "section": "Import / Export", "subsection": "Import Settings",
        "icon": "upload", "tone": "tone-info",
        "keywords": ["import", "apply", "restore", "settings"],
    },
    {
        "tab": "import-export", "setting_id": "cli_cache_prefix",
        "label": "CLI Cache Path Prefix",
        "hint": "The cache path used in your CLI installation (for migration).",
        "section": "Import / Export", "subsection": "CLI Migration",
        "icon": "folder", "tone": "tone-info",
        "keywords": ["cli", "migration", "cache", "path", "prefix", "import"],
    },
    {
        "tab": "import-export", "setting_id": "docker_cache_prefix",
        "label": "Docker Cache Path",
        "hint": "The cache path inside your Docker container (usually /mnt/cache/).",
        "section": "Import / Export", "subsection": "CLI Migration",
        "icon": "folder", "tone": "tone-info",
        "keywords": ["docker", "cache", "path", "container", "migration"],
    },
    {
        "tab": "import-export", "setting_id": "cli_import_btn",
        "label": "Import CLI Data",
        "hint": "Import configuration and tracking data from a CLI installation.",
        "section": "Import / Export", "subsection": "CLI Migration",
        "icon": "upload", "tone": "tone-info",
        "keywords": ["cli", "import", "migration", "data", "tracking"],
    },
]


def get_search_index() -> List[Dict[str, Any]]:
    """Return the searchable settings index.

    The list is returned as-is (no copy). Callers must treat it as read-only.
    """
    return _INDEX

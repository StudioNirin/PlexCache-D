# PlexCache Project Structure

## Overview

PlexCache-R has been restructured into a modular architecture for improved maintainability and organization.

## Current Structure

```
PlexCache-R/
├── plexcache.py              # Unified entry point (auto-setup, --setup flag)
├── core/                     # Core application modules
│   ├── __init__.py           # Package init with version
│   ├── app.py                # Main orchestrator (PlexCacheApp class)
│   ├── setup.py              # Interactive setup wizard
│   ├── config.py             # Configuration management (dataclasses, JSON settings)
│   ├── logging_config.py     # Logging, rotation, Unraid/webhook notification handlers
│   ├── system_utils.py       # OS detection, path conversions, file utilities
│   ├── plex_api.py           # Plex server interactions (OnDeck, Watchlist, RSS feeds)
│   └── file_operations.py    # File moving, filtering, subtitles, timestamp tracking
├── tools/                    # Diagnostic utilities
│   └── audit_cache.py        # Cache diagnostic tool
├── data/                     # Runtime tracking files (auto-created)
│   ├── timestamps.json
│   ├── ondeck_tracker.json
│   ├── watchlist_tracker.json
│   ├── user_tokens.json
│   └── rss_cache.json
├── logs/                     # Log files
├── plexcache_settings.json   # User configuration (root - user-facing)
└── plexcache_mover_files_to_exclude.txt  # Unraid mover exclude list
```

## Module Responsibilities

### `core/app.py` - Main Application
- Application lifecycle management
- Component orchestration
- Error handling and recovery
- Summary generation

### `core/setup.py` - Setup Wizard
- Interactive configuration
- Library-centric path mapping
- Settings migration
- User authentication (OAuth)

### `core/config.py` - Configuration
- Dataclasses for type-safe configuration
- JSON settings loading/saving
- Validation of required fields
- Path mapping management

### `core/logging_config.py` - Logging
- Rotating file handlers
- Unraid notification integration
- Webhook notification support
- Log level management

### `core/system_utils.py` - System Utilities
- OS detection (Linux, Unraid, Docker)
- Cross-platform path conversions
- File operation utilities
- Single instance locking

### `core/plex_api.py` - Plex Integration
- Plex server connections
- OnDeck/Watchlist fetching
- RSS feed fallback for remote users
- Token caching and management

### `core/file_operations.py` - File Operations
- Multi-path file moving
- Subtitle discovery
- Cache timestamp tracking
- Priority-based eviction
- .plexcached backup system

### `tools/audit_cache.py` - Diagnostics
- Cache state analysis
- Orphaned entry detection
- Exclude list validation

## Legacy Entry Points

The following files are deprecated but kept for backwards compatibility:
- `plexcache_app.py` - Redirects to `core/app.py`
- `plexcache_setup.py` - Redirects to `core/setup.py`

## History

This project evolved through several contributors:
1. **brimur** - Original PlexCache concept
2. **bexem** - PlexCache improvements
3. **BBergle** - Major refactoring into modular structure
4. **StudioNirin** - V2.0 features and maintenance
5. **Brandon-Haney** - V2.1 restructuring into `core/` directory

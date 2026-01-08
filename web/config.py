"""Web UI configuration"""

import os
from pathlib import Path

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Project root (parent of web/)
PROJECT_ROOT = WEB_DIR.parent

# Config directory - /config in Docker, project root otherwise
# Docker containers have /.dockerenv or /run/.containerenv
IS_DOCKER = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
CONFIG_DIR = Path("/config") if IS_DOCKER else PROJECT_ROOT

SETTINGS_FILE = CONFIG_DIR / "plexcache_settings.json" if IS_DOCKER else PROJECT_ROOT / "plexcache_settings.json"
LOGS_DIR = CONFIG_DIR / "logs" if IS_DOCKER else PROJECT_ROOT / "logs"
DATA_DIR = CONFIG_DIR / "data" if IS_DOCKER else PROJECT_ROOT / "data"

# Server defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

"""
Jellyfin API integration for PlexCache.
Optional second OnDeck source: Continue Watching + Next Up of every Jellyfin
user, plus active sessions. Jellyfin paths are translated into Plex paths so
the regular path_mappings handle them like any Plex item.
"""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, NamedTuple, Optional

import requests

from core.plex_api import OnDeckItem, PlexManager


REQUEST_TIMEOUT = 30
ITEM_FIELDS = "Path,MediaSources"
# RunTimeTicks are 100ns units
TICKS_PER_MS = 10_000


class ActiveSessions(NamedTuple):
    """Jellyfin playback sessions. count includes sessions whose path has no mapping."""
    count: int
    paths: List[str]


def _normalize_prefix(prefix: str) -> str:
    """Drop trailing slashes so '/media/' and '/media' behave the same; '/' becomes ''."""
    return prefix.rstrip('/')


def _library_hint(path: str) -> str:
    """First two folders of a path (e.g. /media/TV/), enough to tell which mapping is missing."""
    parts = path.lstrip('/').split('/')[:-1][:2]
    return '/' + ''.join(f"{part}/" for part in parts)


def _parse_jellyfin_date(value: Optional[str]) -> Optional[datetime]:
    """Parse a Jellyfin UTC timestamp (7 fractional digits + 'Z') to second precision."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class JellyfinManager:
    """Fetches OnDeck-equivalent media from a Jellyfin server via an admin API key."""

    def __init__(self, url: str, api_key: str, path_mappings: Optional[Dict[str, str]] = None,
                 skip_users: Optional[List[str]] = None, session: Optional[Any] = None):
        self.url = url.rstrip('/')
        # Longest prefix first, so nested mappings win
        self.path_mappings = sorted(
            ((_normalize_prefix(j), _normalize_prefix(p)) for j, p in (path_mappings or {}).items()),
            key=lambda kv: len(kv[0]), reverse=True)
        self.skip_users = set(skip_users or [])
        self.session = session or requests.Session()
        self.session.headers['Authorization'] = f'MediaBrowser Client="PlexCache-D", Token="{api_key}"'
        self._ondeck_data_complete = True
        self._reported_unmapped = set()

    def is_ondeck_data_complete(self) -> bool:
        return self._ondeck_data_complete

    def _get(self, path: str, **params) -> Any:
        response = self.session.get(f"{self.url}{path}", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def to_plex_path(self, path: str) -> Optional[str]:
        """Translate a Jellyfin path into the Plex path space. None if no mapping matches."""
        for jellyfin_prefix, plex_prefix in self.path_mappings:
            # Match whole folders only, so /media does not also match /media2
            if path == jellyfin_prefix or path.startswith(jellyfin_prefix + '/'):
                return plex_prefix + path[len(jellyfin_prefix):]
        return None

    def _item_paths(self, item: Dict[str, Any]) -> List[str]:
        """Plex-space paths of all versions of an item."""
        paths = [s['Path'] for s in item.get('MediaSources') or [] if s.get('Path')]
        if not paths and item.get('Path'):
            paths = [item['Path']]
        result = []
        for path in dict.fromkeys(paths):
            plex_path = self.to_plex_path(path)
            if plex_path:
                result.append(plex_path)
            else:
                self._report_unmapped(path)
        return result

    def _report_unmapped(self, path: str) -> None:
        """Log an unmapped path at INFO once per library folder, so a wrong mapping is visible."""
        logging.debug(f"[JELLYFIN] No jellyfin_path_mappings entry for {path}, skipping")
        hint = _library_hint(path)
        if hint not in self._reported_unmapped:
            self._reported_unmapped.add(hint)
            logging.info(f"[JELLYFIN] Skipping unmapped path {hint} - add it to jellyfin_path_mappings")

    def get_on_deck_media(self, days_to_monitor: int, number_episodes: int,
                          prefetch_minimum_minutes: int = 0) -> List[OnDeckItem]:
        """Continue Watching + Next Up (with the next N episodes) for every enabled user.

        Any failure marks the data incomplete, so move-back is skipped instead of
        evicting media a Jellyfin user is still watching.
        """
        self._ondeck_data_complete = True
        try:
            users = self._get('/Users', isDisabled='false')
        except Exception as e:
            logging.error(f"[JELLYFIN] Failed to list users: {type(e).__name__}: {e}")
            self._ondeck_data_complete = False
            return []

        on_deck_files: List[OnDeckItem] = []
        for user in users:
            username = user.get('Name', '')
            if username in self.skip_users:
                logging.info(f"[JELLYFIN] [USER:{username}] Skipping for OnDeck — in skip list")
                continue
            try:
                on_deck_files.extend(self._fetch_user_on_deck_media(
                    user['Id'], username, days_to_monitor, number_episodes, prefetch_minimum_minutes))
            except Exception as e:
                logging.error(f"[JELLYFIN] [USER:{username}] Failed to fetch OnDeck: {type(e).__name__}: {e}")
                self._ondeck_data_complete = False

        for item in on_deck_files:
            logging.debug(f"[JELLYFIN] [USER:{item.username}] OnDeck found: {item.file_path}")
        return on_deck_files

    def _fetch_user_on_deck_media(self, user_id: str, username: str, days_to_monitor: int,
                                  number_episodes: int, prefetch_minimum_minutes: int) -> List[OnDeckItem]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_monitor)
        resume = self._get_user_items(username, 'Continue Watching', '/UserItems/Resume', userId=user_id,
                                      fields=ITEM_FIELDS, mediaTypes='Video')
        # Next Up filters by the series' last played date server-side
        next_up = self._get_user_items(username, 'Next Up', '/Shows/NextUp', userId=user_id, fields=ITEM_FIELDS,
                                       nextUpDateCutoff=cutoff.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                       enableResumable='true')

        on_deck_files: List[OnDeckItem] = []
        seen_ids = set()
        for item in resume + next_up:
            if item.get('Id') in seen_ids:
                continue
            seen_ids.add(item.get('Id'))
            last_played = _parse_jellyfin_date((item.get('UserData') or {}).get('LastPlayedDate'))
            if last_played and last_played < cutoff:
                continue
            # One failing item must not drop what was already collected for this user
            try:
                if item.get('Type') == 'Episode':
                    self._process_episode_ondeck(item, user_id, username, number_episodes,
                                                 prefetch_minimum_minutes, on_deck_files)
                elif item.get('Type') == 'Movie':
                    for path in self._item_paths(item):
                        on_deck_files.append(OnDeckItem(file_path=path, username=username, is_current_ondeck=True))
                else:
                    logging.debug(f"[JELLYFIN] Skipping OnDeck item '{item.get('Name')}' — type {item.get('Type')}")
            except Exception as e:
                logging.error(f"[JELLYFIN] [USER:{username}] Failed to process OnDeck item "
                              f"'{item.get('SeriesName') or item.get('Name')}': {type(e).__name__}: {e}")
                self._ondeck_data_complete = False
        return on_deck_files

    def _get_user_items(self, username: str, label: str, path: str, **params) -> List[Dict[str, Any]]:
        """Fetch one item list; a failure costs only this list and marks the data incomplete."""
        try:
            return self._get(path, **params).get('Items', [])
        except Exception as e:
            logging.error(f"[JELLYFIN] [USER:{username}] Failed to fetch {label}: {type(e).__name__}: {e}")
            self._ondeck_data_complete = False
            return []

    def _process_episode_ondeck(self, item: Dict[str, Any], user_id: str, username: str,
                                number_episodes: int, prefetch_minimum_minutes: int,
                                on_deck_files: List[OnDeckItem]) -> None:
        show = item.get('SeriesName')
        season = item.get('ParentIndexNumber')
        episode = item.get('IndexNumber')
        episode_info = None
        if season is not None and episode is not None:
            episode_info = {'show': show, 'season': season, 'episode': episode}

        for path in self._item_paths(item):
            on_deck_files.append(OnDeckItem(file_path=path, username=username,
                                            episode_info=episode_info, is_current_ondeck=True))

        if episode_info is None or not item.get('SeriesId'):
            logging.warning(f"[JELLYFIN] Skipping next episode fetch for '{show}' - missing index data")
            return

        # startItemId skips everything before the current episode, keeping long shows small
        episodes = self._get(f"/Shows/{item['SeriesId']}/Episodes", userId=user_id, startItemId=item['Id'],
                             fields=ITEM_FIELDS, isMissing='false').get('Items', [])
        # Shape Jellyfin episodes like plexapi ones to share the prefetch logic
        candidates = [
            SimpleNamespace(parentIndex=e.get('ParentIndexNumber'), index=e.get('IndexNumber'),
                            title=e.get('Name'), grandparentTitle=show,
                            duration=(e.get('RunTimeTicks') or 0) // TICKS_PER_MS, item=e)
            for e in episodes
        ]
        next_episodes = PlexManager._get_next_episodes(
            candidates, season, episode, number_episodes,
            prefetch_minimum_minutes=prefetch_minimum_minutes)
        for next_episode in next_episodes:
            next_info = {'show': show, 'season': next_episode.parentIndex, 'episode': next_episode.index}
            for path in self._item_paths(next_episode.item):
                on_deck_files.append(OnDeckItem(file_path=path, username=username,
                                                episode_info=next_info, is_current_ondeck=False))

    def get_active_sessions(self) -> ActiveSessions:
        """Everything currently playing on Jellyfin, with Plex-space paths where a mapping exists."""
        count = 0
        paths = []
        try:
            for session in self._get('/Sessions'):
                item = session.get('NowPlayingItem')
                if not item:
                    continue
                count += 1
                logging.debug(f"[JELLYFIN] Active session detected, skipping: {item.get('SeriesName') or item.get('Name')}")
                item_paths = self._item_paths(item)
                if not item_paths and not item.get('Path') and not item.get('MediaSources') and item.get('Id'):
                    # NowPlayingItem may come without path fields
                    items = self._get('/Items', ids=item['Id'], userId=session.get('UserId'),
                                      fields=ITEM_FIELDS).get('Items', [])
                    item_paths = [p for i in items for p in self._item_paths(i)]
                paths.extend(item_paths)
        except Exception as e:
            logging.warning(f"[JELLYFIN] Failed to fetch active sessions: {type(e).__name__}: {e}")
        return ActiveSessions(count, paths)

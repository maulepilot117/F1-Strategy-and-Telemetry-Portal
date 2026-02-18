"""
FastF1 cache configuration.

FastF1 downloads data from the F1 live timing API and caches it locally so
subsequent requests are instant.  The cache directory must exist before
FastF1 will use it, so this module creates it if needed.
"""

from pathlib import Path

import fastf1

# Store the cache inside backend/.fastf1_cache (ignored by git)
_CACHE_DIR = Path(__file__).resolve().parent.parent / ".fastf1_cache"

_cache_enabled = False


def setup_cache(cache_dir: Path | None = None) -> Path:
    """Ensure the FastF1 cache is enabled and return the cache directory path.

    Call this once at startup, before loading any session data.
    Repeated calls are safe — the cache is only initialised on the first call.

    Args:
        cache_dir: Override the default cache location.  Useful for tests.

    Returns:
        The resolved path to the cache directory.
    """
    global _cache_enabled

    if _cache_enabled:
        return _CACHE_DIR

    path = cache_dir or _CACHE_DIR
    path.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(path))
    _cache_enabled = True
    return path

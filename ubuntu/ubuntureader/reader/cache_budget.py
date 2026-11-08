"""Budget for retained reading data, not an OS process memory limit."""
DEFAULT_CACHE_MB = 128
MIN_CACHE_MB = 32
MAX_CACHE_MB = 512


def cache_budget_mb(library):
    value = library.setting('reader_cache_mb', DEFAULT_CACHE_MB)
    if type(value) not in (int, float):
        return DEFAULT_CACHE_MB
    return max(MIN_CACHE_MB, min(MAX_CACHE_MB, int(value)))

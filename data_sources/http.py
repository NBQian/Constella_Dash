"""Shared HTTP helper with retry/backoff for rate-limited public APIs."""
import logging
import time

import requests

log = logging.getLogger(__name__)
_session = requests.Session()


def get_json(url, params=None, headers=None, retries=5, backoff=15.0, timeout=60):
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            log.warning("%s failed (%s), retrying", url, e)
            time.sleep(backoff)
            continue
        # CoinGecko's public API answers throttled clients with 403 as well as 429.
        if r.status_code in (403, 429) or r.status_code >= 500:
            wait = backoff * (attempt + 1)
            log.warning("%s -> HTTP %s, sleeping %.0fs", url, r.status_code, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"GET {url} failed after {retries} attempts")

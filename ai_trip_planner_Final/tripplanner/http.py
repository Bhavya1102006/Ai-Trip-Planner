"""Small JSON-over-HTTP helper (standard library only) shared by every cloud client.

Retries on rate limits and server errors with a short back-off. Error messages never
include request headers, so API keys cannot leak into logs or responses.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Union

from .errors import CloudServiceError

log = logging.getLogger("tripplanner")
RETRY_STATUS = {429, 500, 502, 503, 504}
# Overpass (and some other services) reject requests that do not identify themselves
USER_AGENT = "ai-trip-planner/0.2 (student project; Python urllib)"

# every client takes a callable with this shape, so tests can pass a fake
Fetch = Callable[..., dict]


def request_json(method: str, url: str, headers: Optional[Dict[str, str]] = None,
                 body: Union[dict, str, None] = None, timeout: float = 15.0, retries: int = 2,
                 _open=urllib.request.urlopen, _sleep=time.sleep) -> dict:
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", USER_AGENT)
    hdrs.setdefault("Accept", "application/json")
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        data = body.encode("utf-8")
    else:
        data = None
    host = urllib.request.urlparse(url).netloc

    last = "unknown error"
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with _open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code not in RETRY_STATUS:
                raise CloudServiceError(f"{host} returned {last}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"network error ({type(exc).__name__})"
        except json.JSONDecodeError as exc:
            raise CloudServiceError(f"{host} returned invalid JSON") from exc
        if attempt < retries:
            log.warning("%s: %s, retrying (%d/%d)", host, last, attempt + 1, retries)
            _sleep(0.5 * (2 ** attempt))
    raise CloudServiceError(f"{host} failed after {retries + 1} attempts: {last}")

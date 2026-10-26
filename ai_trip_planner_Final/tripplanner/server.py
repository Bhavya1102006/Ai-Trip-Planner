"""Minimal JSON web API (WSGI, standard library only).

  GET  /health                      -> service status (no secrets)
  POST /plan    {"brief": "...", "user_id": "...", "save": true, "llm": true, "live": true}
  GET  /trips?user_id=...           -> that user's saved trips
  GET  /trips/<id>?user_id=...      -> one saved trip

Run locally:   python -m tripplanner.server
Deploy:        gunicorn tripplanner.server:app        (see render.yaml / Procfile)

Security notes: set API_AUTH_TOKEN so only your front end can call it. user_id is an
opaque label chosen by the client, NOT a login: use a real auth provider before storing
anything private.
"""
import hmac
import json
import logging
import mimetypes
import os
import re
from typing import Callable, Optional
from urllib.parse import parse_qs

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

from .config import Settings
from .errors import (CloudServiceError, InfeasiblePlanError, InvalidBriefError, PlannerError,
                     UnknownDestinationError)
from .http import request_json
from .service import run_plan
from .storage import itinerary_to_dict, open_store

log = logging.getLogger("tripplanner")
MAX_BODY = 10_000
USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def create_app(settings: Settings, fetch: Callable = request_json, store=None):
    store = store or open_store(settings.database_url)

    def respond(start, status: str, payload: dict, extra_headers=()):
        body = json.dumps(payload).encode("utf-8")
        headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                   ("X-Content-Type-Options", "nosniff"), *extra_headers]
        if settings.allowed_origin:
            headers += [("Access-Control-Allow-Origin", settings.allowed_origin),
                        ("Access-Control-Allow-Headers", "Content-Type, Authorization")]
        start(status, headers)
        return [body]

    def authorised(environ) -> bool:
        if not settings.auth_token:
            return True
        given = environ.get("HTTP_AUTHORIZATION", "")
        return hmac.compare_digest(given, f"Bearer {settings.auth_token}")

    def read_json(environ) -> dict:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            raise InvalidBriefError("Request body is missing or too large.")
        try:
            data = json.loads(environ["wsgi.input"].read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise InvalidBriefError("Request body is not valid JSON.")
        if not isinstance(data, dict):
            raise InvalidBriefError("Request body must be a JSON object.")
        return data

    def clean_user(value) -> str:
        if not isinstance(value, str) or not USER_RE.match(value):
            raise InvalidBriefError("user_id must be 1-64 letters, digits or _ . @ -")
        return value

    def serve_static(start, file_path):
        """Serve a file from the static/ directory with the correct MIME type."""
        try:
            with open(file_path, "rb") as fh:
                body = fh.read()
            mime, _ = mimetypes.guess_type(file_path)
            mime = mime or "application/octet-stream"
            start("200 OK", [("Content-Type", mime), ("Content-Length", str(len(body)))])
            return [body]
        except (OSError, IOError):
            start("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not found"]

    def app(environ, start_response):
        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/")
        if method == "OPTIONS":
            return respond(start_response, "204 No Content", {})

        # ── Static file serving (no auth required) ──────────────────────────
        if method == "GET" and (path == "/" or path == "/index.html"):
            return serve_static(start_response, os.path.join(STATIC_DIR, "index.html"))
        if method == "GET" and path in ("/style.css", "/app.js"):
            return serve_static(start_response, os.path.join(STATIC_DIR, path.lstrip("/")))
        if method == "GET" and path.startswith("/static/"):
            rel = os.path.normpath(path[len("/static/"):])
            # Safety: prevent directory traversal
            full = os.path.realpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(os.path.realpath(STATIC_DIR)):
                start_response("403 Forbidden", [("Content-Type", "text/plain")])
                return [b"Forbidden"]
            return serve_static(start_response, full)

        if path == "/health":
            return respond(start_response, "200 OK", {
                "status": "ok", "llm": settings.llm_provider or False,
                "places": "google" if settings.google_key else ("opentripmap" if settings.opentripmap_key else "openstreetmap"),
                "database": store.name})
        if not authorised(environ):
            return respond(start_response, "401 Unauthorized", {"error": "Missing or wrong token."})
        try:
            if method == "POST" and path == "/plan":
                data = read_json(environ)
                text = data.get("brief")
                if not isinstance(text, str):
                    raise InvalidBriefError("'brief' must be a string.")
                result = run_plan(text, settings, use_llm=bool(data.get("llm")),
                                  places_mode="live" if data.get("live") else "local", fetch=fetch)
                plan = itinerary_to_dict(result.itinerary)
                out = {"plan": plan, "explanations": result.explanations,
                       "meta": {"parser": result.parser, "places": result.places_source,
                                "explainer": result.explainer}}
                if data.get("save"):
                    out["trip_id"] = store.save_trip(clean_user(data.get("user_id")), text, plan)
                return respond(start_response, "200 OK", out)

            if method == "GET" and path == "/trips":
                q = parse_qs(environ.get("QUERY_STRING", ""))
                user = clean_user((q.get("user_id") or [None])[0])
                return respond(start_response, "200 OK", {"trips": store.list_trips(user)})

            m = re.fullmatch(r"/trips/(\d+)", path)
            if method == "GET" and m:
                q = parse_qs(environ.get("QUERY_STRING", ""))
                trip = store.get_trip(int(m.group(1)), clean_user((q.get("user_id") or [None])[0]))
                if trip is None:
                    return respond(start_response, "404 Not Found", {"error": "Trip not found."})
                return respond(start_response, "200 OK", trip)

            return respond(start_response, "404 Not Found", {"error": "No such endpoint."})
        except (InvalidBriefError, InfeasiblePlanError) as exc:
            return respond(start_response, "400 Bad Request", {"error": str(exc)})
        except UnknownDestinationError as exc:
            return respond(start_response, "404 Not Found", {"error": str(exc)})
        except CloudServiceError as exc:
            log.error("cloud service error: %s", exc)
            return respond(start_response, "502 Bad Gateway", {"error": "An upstream service failed."})
        except PlannerError as exc:
            return respond(start_response, "400 Bad Request", {"error": str(exc)})
        except Exception:                            # never leak internals to the client
            log.exception("unhandled error")
            return respond(start_response, "500 Internal Server Error", {"error": "Internal error."})

    return app



_app = None


def app(environ, start_response):
    """Module-level entry point for gunicorn; builds the real app on first request."""
    global _app
    if _app is None:
        _app = create_app(Settings.from_env())
    return _app(environ, start_response)


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
    cfg = Settings.from_env()
    print(f"Serving on http://0.0.0.0:{cfg.port}  (dev server; use gunicorn in production)")
    make_server("0.0.0.0", cfg.port, create_app(cfg)).serve_forever()

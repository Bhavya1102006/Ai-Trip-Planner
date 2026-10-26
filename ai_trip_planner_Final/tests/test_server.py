import io
import json
import unittest
from wsgiref.util import setup_testing_defaults

from tripplanner.config import Settings
from tripplanner.server import create_app
from tripplanner.storage import SQLiteStore, itinerary_to_dict, open_store
from tripplanner.errors import CloudServiceError
from tripplanner.service import run_plan
from tests.test_cloud import FakeFetch, claude_reply


def call(app, method, path, body=None, headers=None, query=""):
    env = {}
    setup_testing_defaults(env)
    raw = json.dumps(body).encode() if body is not None else b""
    env.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING=query,
               CONTENT_LENGTH=str(len(raw)), **{"wsgi.input": io.BytesIO(raw)})
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    out = {}
    chunks = app(env, lambda status, hdrs: out.update(status=status, headers=dict(hdrs)))
    data = b"".join(chunks)
    return out["status"], out["headers"], (json.loads(data) if data else None)


BRIEF = "2 days in Jaipur, budget 2500 per day, heritage and food"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(":memory:")
        self.plan = itinerary_to_dict(run_plan(BRIEF, Settings()).itinerary)

    def test_save_and_read_back(self):
        tid = self.store.save_trip("alice", BRIEF, self.plan)
        got = self.store.get_trip(tid, "alice")
        self.assertEqual(got["plan"], self.plan)
        self.assertEqual(got["brief"], BRIEF)

    def test_other_user_cannot_read_trip(self):
        tid = self.store.save_trip("alice", BRIEF, self.plan)
        self.assertIsNone(self.store.get_trip(tid, "bob"))

    def test_list_is_per_user_newest_first(self):
        a = self.store.save_trip("alice", "one", self.plan)
        b = self.store.save_trip("alice", "two", self.plan)
        self.store.save_trip("bob", "other", self.plan)
        self.assertEqual([t["id"] for t in self.store.list_trips("alice")], [b, a])

    def test_sql_injection_text_is_stored_as_plain_data(self):
        evil = "x'); DROP TABLE trips;--"
        tid = self.store.save_trip(evil, evil, self.plan)
        self.assertEqual(self.store.get_trip(tid, evil)["brief"], evil)

    def test_open_store_rejects_unknown_scheme(self):
        with self.assertRaises(CloudServiceError):
            open_store("mysql://x")
        self.assertEqual(open_store("sqlite:///:memory:").name, "sqlite")


class ApiTests(unittest.TestCase):
    def make(self, **kw):
        return create_app(Settings(**kw), fetch=kw.pop("fetch", None) or FakeFetch({}), store=SQLiteStore(":memory:"))

    def test_health_has_no_secrets(self):
        app = self.make(anthropic_key="SECRETKEY", google_key="OTHERSECRET")
        status, _, data = call(app, "GET", "/health")
        self.assertEqual(status, "200 OK")
        self.assertNotIn("SECRET", json.dumps(data))
        self.assertTrue(data["llm"])

    def test_plan_and_save_and_fetch(self):
        app = self.make()
        status, _, out = call(app, "POST", "/plan", {"brief": BRIEF, "user_id": "u1", "save": True})
        self.assertEqual(status, "200 OK")
        self.assertEqual(len(out["plan"]["days"]), 2)
        self.assertEqual(out["meta"]["parser"], "rules")
        _, _, listing = call(app, "GET", "/trips", query="user_id=u1")
        self.assertEqual(len(listing["trips"]), 1)
        status, _, one = call(app, "GET", f"/trips/{out['trip_id']}", query="user_id=u1")
        self.assertEqual(one["plan"], out["plan"])
        status, _, _ = call(app, "GET", f"/trips/{out['trip_id']}", query="user_id=intruder")
        self.assertEqual(status, "404 Not Found")

    def test_bad_brief_is_400_with_message(self):
        status, _, out = call(self.make(), "POST", "/plan", {"brief": "hello"})
        self.assertEqual(status, "400 Bad Request")
        self.assertIn("destination", out["error"])

    def test_bad_json_and_wrong_types(self):
        app = self.make()
        env = {}
        setup_testing_defaults(env)
        env.update(REQUEST_METHOD="POST", PATH_INFO="/plan", CONTENT_LENGTH="5", **{"wsgi.input": io.BytesIO(b"{oops")})
        out = {}
        app(env, lambda s, h: out.update(status=s))
        self.assertEqual(out["status"], "400 Bad Request")
        self.assertEqual(call(app, "POST", "/plan", {"brief": 123})[0], "400 Bad Request")
        self.assertEqual(call(app, "POST", "/plan", ["x"])[0], "400 Bad Request")

    def test_oversized_body_rejected(self):
        self.assertEqual(call(self.make(), "POST", "/plan", {"brief": "x" * 20000})[0], "400 Bad Request")

    def test_bad_user_id_rejected(self):
        status, _, _ = call(self.make(), "POST", "/plan", {"brief": BRIEF, "save": True, "user_id": "a b; DROP"})
        self.assertEqual(status, "400 Bad Request")

    def test_unknown_city_is_404(self):
        status, _, _ = call(self.make(), "POST", "/plan", {"brief": "2 days in Atlantis, budget 3000"})
        self.assertEqual(status, "404 Not Found")

    def test_auth_token_enforced(self):
        app = self.make(auth_token="s3cret")
        self.assertEqual(call(app, "POST", "/plan", {"brief": BRIEF})[0], "401 Unauthorized")
        self.assertEqual(call(app, "POST", "/plan", {"brief": BRIEF}, headers={"Authorization": "Bearer wrong"})[0], "401 Unauthorized")
        self.assertEqual(call(app, "POST", "/plan", {"brief": BRIEF}, headers={"Authorization": "Bearer s3cret"})[0], "200 OK")
        self.assertEqual(call(app, "GET", "/health")[0], "200 OK")        # health stays open for the host's checks

    def test_cors_header_only_when_configured(self):
        self.assertNotIn("Access-Control-Allow-Origin", call(self.make(), "GET", "/health")[1])
        app = self.make(allowed_origin="https://my.app")
        self.assertEqual(call(app, "GET", "/health")[1]["Access-Control-Allow-Origin"], "https://my.app")

    def test_upstream_failure_is_502_without_details(self):
        fake = FakeFetch({"https://api.anthropic.com": CloudServiceError("api.anthropic.com HTTP 500")})
        app = create_app(Settings(anthropic_key="k"), fetch=fake, store=SQLiteStore(":memory:"))
        # LLM parsing falls back to rules and the explainer to the template, so the plan still succeeds
        status, _, out = call(app, "POST", "/plan", {"brief": BRIEF, "llm": True})
        self.assertEqual(status, "200 OK")
        self.assertEqual((out["meta"]["parser"], out["meta"]["explainer"]), ("rules", "template"))

    def test_full_cloud_path_with_fakes(self):
        reply = {"destination": "Jaipur", "days": 2, "budget_per_day": 2500, "interests": ["heritage"], "pace": "moderate"}
        def anthropic(body):
            if "extract trip parameters" in body.get("system", ""):
                return claude_reply(json.dumps(reply))
            return claude_reply("A relaxed heritage day.")
        from tests.test_cloud import google_place
        from tripplanner import sources
        wd = sources.default_weekday()
        places = {"places": [google_place(f"id{i}", f"Fort {i}", day=wd) for i in range(12)]}
        sources._cache = sources.MemoryCache()
        fake = FakeFetch({"https://api.anthropic.com": anthropic, "https://places.googleapis.com": places})
        app = create_app(Settings(anthropic_key="k", google_key="g"), fetch=fake, store=SQLiteStore(":memory:"))
        status, _, out = call(app, "POST", "/plan", {"brief": "two days jaipur forts please", "llm": True, "live": True})
        self.assertEqual(status, "200 OK")
        self.assertEqual(out["meta"], {"parser": "llm", "places": "Google Places", "explainer": "llm"})
        self.assertEqual(out["explanations"][0], "A relaxed heritage day.")

    def test_unknown_route_404(self):
        self.assertEqual(call(self.make(), "GET", "/nope")[0], "404 Not Found")


if __name__ == "__main__":
    unittest.main()

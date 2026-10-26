"""Cloud-layer tests. No network: every service call goes through a fake `fetch`."""
import io
import json
import unittest
from unittest import mock

from tripplanner.config import Settings
from tripplanner.errors import CloudServiceError, InvalidBriefError
from tripplanner.http import request_json
from tripplanner.llm import AnthropicClient, parse_brief_llm, parse_brief_smart
from tripplanner.models import TripBrief
from tripplanner.places import (GooglePlacesClient, MemoryCache, OverpassClient, google_hours,
                                google_place_to_poi, osm_hours)
from tripplanner.explainer import FallbackExplainer, LLMExplainer
from tripplanner.service import run_plan
from tripplanner import sources


def google_place(pid="p1", name="Test Fort", types=("tourist_attraction",), count=9000, day=1,
                 price=None, with_hours=True):
    p = {"id": pid, "displayName": {"text": name}, "location": {"latitude": 26.9, "longitude": 75.8},
         "types": list(types), "rating": 4.6, "userRatingCount": count}
    if price:
        p["priceLevel"] = price
    if with_hours:
        p["regularOpeningHours"] = {"periods": [{"open": {"day": day, "hour": 9, "minute": 0},
                                                 "close": {"day": day, "hour": 17, "minute": 30}}]}
    return p


class FakeFetch:
    """Records calls and returns canned JSON chosen by URL."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, body=None, **kw):
        self.calls.append((method, url, headers, body))
        for prefix, reply in self.routes.items():
            if url.startswith(prefix):
                if isinstance(reply, Exception):
                    raise reply
                return reply(body) if callable(reply) else reply
        raise AssertionError("unexpected url " + url)


class GooglePlacesTests(unittest.TestCase):
    def test_converts_place(self):
        poi = google_place_to_poi(google_place(price="PRICE_LEVEL_MODERATE"), weekday=1)
        self.assertEqual((poi.open_min, poi.close_min, poi.cost, poi.mainstream), (540, 1050, 500, True))
        self.assertIn("heritage", poi.tags)

    def test_low_review_count_is_non_mainstream(self):
        self.assertFalse(google_place_to_poi(google_place(count=40), 1).mainstream)

    def test_closed_that_weekday_is_skipped(self):
        self.assertIsNone(google_place_to_poi(google_place(day=1), weekday=3))

    def test_missing_hours_get_default(self):
        poi = google_place_to_poi(google_place(with_hours=False), 1)
        self.assertEqual((poi.open_min, poi.close_min), (540, 1080))

    def test_open_all_day_and_past_midnight(self):
        self.assertEqual(google_hours({"periods": [{"open": {"day": 2, "hour": 0, "minute": 0}}]}, 2), (0, 1440))
        late = {"periods": [{"open": {"day": 2, "hour": 18}, "close": {"day": 3, "hour": 2}}]}
        self.assertEqual(google_hours(late, 2), (1080, 1440))

    def test_unmapped_type_and_missing_fields_skipped(self):
        self.assertIsNone(google_place_to_poi(google_place(types=("atm",)), 1))
        self.assertIsNone(google_place_to_poi({"id": "x", "displayName": {"text": "No location"}}, 1))

    def test_free_tags_cost_zero_without_price(self):
        self.assertEqual(google_place_to_poi(google_place(types=("park",)), 1).cost, 0)

    def test_search_sends_key_and_field_mask_and_dedupes(self):
        fake = FakeFetch({"https://places.googleapis.com": {"places": [google_place("a"), google_place("a"), google_place("b", "Other")]}})
        pois = GooglePlacesClient("SECRET", fake).search("Jaipur", ["heritage", "food"], 1)
        self.assertEqual(len(pois), 2)
        _, _, headers, body = fake.calls[0]
        self.assertEqual(headers["X-Goog-Api-Key"], "SECRET")
        self.assertIn("places.regularOpeningHours", headers["X-Goog-FieldMask"])
        self.assertIn("Jaipur", body["textQuery"])
        self.assertEqual(len(fake.calls), 2)          # one query per interest

    def test_missing_key_rejected(self):
        with self.assertRaises(CloudServiceError):
            GooglePlacesClient("")


class OverpassTests(unittest.TestCase):
    def test_osm_hours(self):
        self.assertEqual(osm_hours("Mo-Su 09:00-17:30"), (540, 1050))
        self.assertEqual(osm_hours("sunrise-sunset"), (540, 1080))

    NOMINATIM = [{"lat": "18.52", "lon": "73.85"}]
    ELEMENTS = {"elements": [
        {"type": "node", "id": 1, "lat": 18.52, "lon": 73.85, "tags": {"name": "Old Fort", "historic": "fort", "wikidata": "Q1"}},
        {"type": "way", "id": 2, "center": {"lat": 18.53, "lon": 73.86}, "tags": {"name": "Green Park", "leisure": "park"}},
        {"type": "node", "id": 3, "lat": 18.5, "lon": 73.8, "tags": {"name": "Shop", "shop": "bakery"}},
        {"type": "node", "id": 4, "lat": 18.5, "lon": 73.8, "tags": {"historic": "fort"}}]}

    def test_search_parses_elements(self):
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM, "https://overpass-api.de": self.ELEMENTS})
        pois = OverpassClient(fake).search("Pune", [], 1)
        self.assertEqual({p.name for p in pois}, {"Old Fort", "Green Park"})
        self.assertTrue(next(p for p in pois if p.name == "Old Fort").mainstream)

    def test_query_is_a_small_box_around_the_city_centre(self):
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM, "https://overpass-api.de": self.ELEMENTS})
        OverpassClient(fake).search("Pune", [], 1)
        from urllib.parse import unquote
        query = unquote(fake.calls[1][3])
        self.assertIn("(18.47000,73.80000,18.57000,73.90000)", query)
        self.assertNotIn("area[", query)

    def test_query_only_asks_for_what_the_traveller_wants(self):
        from urllib.parse import unquote
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM, "https://overpass-api.de": self.ELEMENTS})
        OverpassClient(fake).search("Pune", ["food"], 1)
        q = unquote(fake.calls[1][3])
        self.assertIn("restaurant", q)
        self.assertNotIn("historic", q)
        self.assertNotIn("place_of_worship", q)

    def test_no_interests_asks_for_everything(self):
        from urllib.parse import unquote
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM, "https://overpass-api.de": self.ELEMENTS})
        OverpassClient(fake).search("Pune", [], 1)
        q = unquote(fake.calls[1][3])
        self.assertTrue("historic" in q and "restaurant" in q and "park" in q)

    def test_destination_text_never_reaches_the_overpass_query(self):
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM, "https://overpass-api.de": self.ELEMENTS})
        OverpassClient(fake).search('Pune"; out; evil', [], 1)
        self.assertNotIn("evil", fake.calls[1][3])

    def test_falls_through_to_next_mirror(self):
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM,
                          "https://overpass-api.de": CloudServiceError("HTTP 502"),
                          "https://overpass.kumi.systems": self.ELEMENTS})
        self.assertEqual(len(OverpassClient(fake).search("Pune", [], 1)), 2)

    def test_all_mirrors_down_raises(self):
        fake = FakeFetch({"https://nominatim.openstreetmap.org": self.NOMINATIM,
                          "https://overpass-api.de": CloudServiceError("a"),
                          "https://overpass.kumi.systems": CloudServiceError("b"),
                          "https://overpass.private.coffee": CloudServiceError("c")})
        with self.assertRaises(CloudServiceError):
            OverpassClient(fake).search("Pune", [], 1)

    def test_unknown_place_name_raises(self):
        fake = FakeFetch({"https://nominatim.openstreetmap.org": []})
        with self.assertRaises(CloudServiceError):
            OverpassClient(fake).search("Nowhereville", [], 1)


class SourcesTests(unittest.TestCase):
    def setUp(self):
        sources._cache = MemoryCache()
        self.brief = TripBrief("jaipur", 2, 3000, ["heritage"], "moderate")

    def many(self, n):
        return {"places": [google_place(f"id{i}", f"Place {i}", day=sources.default_weekday()) for i in range(n)]}

    def test_local_mode(self):
        pois, src = sources.get_pois(self.brief, Settings(), "local")
        self.assertEqual((len(pois), src), (24, "local file"))

    def test_live_uses_google_when_key_set(self):
        fake = FakeFetch({"https://places.googleapis.com": self.many(10)})
        pois, src = sources.get_pois(self.brief, Settings(google_key="k"), "live", fake)
        self.assertEqual((len(pois), src), (10, "Google Places"))

    def test_live_without_key_uses_overpass(self):
        els = {"elements": [{"type": "node", "id": i, "lat": 26.9, "lon": 75.8,
                             "tags": {"name": f"P{i}", "historic": "fort"}} for i in range(9)]}
        fake = FakeFetch({"https://nominatim.openstreetmap.org": [{"lat": "26.9", "lon": "75.8"}], "https://overpass-api.de": els})
        pois, src = sources.get_pois(self.brief, Settings(), "live", fake)
        self.assertEqual(src, "OpenStreetMap")

    def test_live_failure_falls_back_to_local_file(self):
        fake = FakeFetch({"https://places.googleapis.com": CloudServiceError("boom"),
                          "https://nominatim.openstreetmap.org": CloudServiceError("down")})
        pois, src = sources.get_pois(self.brief, Settings(google_key="k"), "live", fake)
        self.assertEqual(src, "local file (fallback)")

    def test_live_failure_for_city_without_local_data_raises(self):
        fake = FakeFetch({"https://places.googleapis.com": CloudServiceError("boom"),
                          "https://nominatim.openstreetmap.org": CloudServiceError("down")})
        with self.assertRaises(Exception) as cm:
            sources.get_pois(TripBrief("pune", 1, 2000, ["food"]), Settings(google_key="k"), "live", fake)
        self.assertIn("No place data", str(cm.exception))

    def test_second_call_hits_memory_cache(self):
        fake = FakeFetch({"https://places.googleapis.com": self.many(10)})
        sources.get_pois(self.brief, Settings(google_key="k"), "live", fake)
        n = len(fake.calls)
        sources.get_pois(self.brief, Settings(google_key="k"), "live", fake)
        self.assertEqual(len(fake.calls), n)

    def test_cache_expires(self):
        t = [0]
        c = MemoryCache(10, clock=lambda: t[0])
        c.put("k", 1); t[0] = 11
        self.assertIsNone(c.get("k"))


def claude_reply(text):
    return {"content": [{"type": "text", "text": text}]}


class LLMTests(unittest.TestCase):
    GOOD = '{"destination":"Jaipur","days":3,"budget_per_day":3000,"interests":["food","heritage"],"pace":"relaxed"}'

    def client(self, reply):
        fake = FakeFetch({"https://api.anthropic.com": reply})
        return AnthropicClient("KEY", "claude-sonnet-5-5", fake), fake

    def test_request_shape(self):
        c, fake = self.client(claude_reply("hi"))
        self.assertEqual(c.complete("p", system="s"), "hi")
        _, url, headers, body = fake.calls[0]
        self.assertEqual(headers["x-api-key"], "KEY")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual((body["model"], body["system"]), ("claude-sonnet-5-5", "s"))

    def test_empty_reply_is_an_error(self):
        c, _ = self.client({"content": []})
        with self.assertRaises(CloudServiceError):
            c.complete("p")

    def test_parse_valid_json_even_in_code_fence(self):
        c, _ = self.client(claude_reply("```json\n" + self.GOOD + "\n```"))
        b = parse_brief_llm("anything", c)
        self.assertEqual((b.destination, b.days, b.pace), ("jaipur", 3, "relaxed"))

    def test_model_output_goes_through_validation(self):
        c, _ = self.client(claude_reply('{"destination":"Jaipur","days":99,"budget_per_day":3000,"interests":[],"pace":"x"}'))
        with self.assertRaises(InvalidBriefError):
            parse_brief_llm("anything", c)

    def test_non_integer_fields_rejected(self):
        c, _ = self.client(claude_reply('{"destination":"Jaipur","days":"3","budget_per_day":3000}'))
        with self.assertRaises(InvalidBriefError):
            parse_brief_llm("anything", c)

    def test_unknown_interests_dropped_not_trusted(self):
        c, _ = self.client(claude_reply('{"destination":"Jaipur","days":2,"budget_per_day":2000,"interests":["hacking","food"]}'))
        self.assertEqual(parse_brief_llm("x", c).interests, ["food"])

    def test_garbage_reply_raises_cloud_error(self):
        c, _ = self.client(claude_reply("Sorry, I can't help."))
        with self.assertRaises(CloudServiceError):
            parse_brief_llm("x", c)

    def test_long_input_is_truncated_before_sending(self):
        c, fake = self.client(claude_reply(self.GOOD))
        parse_brief_llm("a" * 5000, c)
        self.assertLessEqual(len(fake.calls[0][3]["messages"][0]["content"]), 500)

    def test_smart_parser_falls_back_to_rules_when_service_down(self):
        c, _ = self.client(CloudServiceError("down"))
        b, who = parse_brief_smart("2 days in Jaipur, budget 2500 per day, food", c)
        self.assertEqual((who, b.days), ("rules", 2))

    def test_smart_parser_uses_llm(self):
        c, _ = self.client(claude_reply(self.GOOD))
        self.assertEqual(parse_brief_smart("whatever", c)[1], "llm")

    def test_invalid_brief_is_not_silently_retried_with_rules(self):
        c, _ = self.client(claude_reply('{"destination":"Jaipur","days":50,"budget_per_day":3000}'))
        with self.assertRaises(InvalidBriefError):
            parse_brief_smart("50 days", c)


def groq_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class GroqTests(unittest.TestCase):
    def client(self, reply):
        from tripplanner.llm import GroqClient
        fake = FakeFetch({"https://api.groq.com": reply})
        return GroqClient("gsk_SECRET", "some-model", fake), fake

    def test_request_shape(self):
        c, fake = self.client(groq_reply("hello"))
        self.assertEqual(c.complete("question", system="be brief"), "hello")
        method, url, headers, body = fake.calls[0]
        self.assertEqual(url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer gsk_SECRET")
        self.assertEqual(body["model"], "some-model")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])

    def test_empty_or_reasoning_only_reply_is_an_error(self):
        for reply in ({"choices": []}, groq_reply(""), {"choices": [{"message": {"content": None, "reasoning": "hmm"}}]}, {}):
            c, _ = self.client(reply)
            with self.assertRaises(CloudServiceError):
                c.complete("q")

    def test_parses_a_brief_through_validation(self):
        good = '{"destination":"Pune","days":3,"budget_per_day":3000,"interests":["food","nature"],"pace":"moderate"}'
        c, _ = self.client(groq_reply(good))
        b = parse_brief_llm("x", c)
        self.assertEqual((b.destination, b.days), ("pune", 3))

    def test_list_models(self):
        c, _ = self.client({"data": [{"id": "b-model"}, {"id": "a-model"}]})
        self.assertEqual(c.list_models(), ["a-model", "b-model"])

    def test_provider_selection(self):
        from tripplanner.llm import AnthropicClient, GroqClient, client_from_settings
        self.assertIsNone(client_from_settings(Settings()))
        self.assertIsInstance(client_from_settings(Settings(groq_key="g")), GroqClient)
        self.assertIsInstance(client_from_settings(Settings(anthropic_key="a")), AnthropicClient)
        both = dict(groq_key="g", anthropic_key="a")
        self.assertIsInstance(client_from_settings(Settings(**both)), AnthropicClient)
        self.assertIsInstance(client_from_settings(Settings(llm_provider_pref="groq", **both)), GroqClient)

    def test_settings_read_groq_env(self):
        s = Settings.from_env({"GROQ_API_KEY": "gsk_x", "GROQ_MODEL": "m"})
        self.assertEqual((s.groq_key, s.groq_model, s.llm_provider), ("gsk_x", "m", "groq"))


OTM_ITEM = {"xid": "N1", "name": "Shaniwar Wada", "kinds": "historic,fortifications,castles,interesting_places",
            "rate": "3h", "point": {"lon": 73.85, "lat": 18.51}}


def otm_items(n, kinds="historic,fortifications"):
    return [{"xid": f"X{i}", "name": f"Place {i}", "kinds": kinds, "rate": 2,
             "point": {"lon": 73.8 + i / 1000, "lat": 18.5}} for i in range(n)]


class OpenTripMapTests(unittest.TestCase):
    GEO = {"status": "OK", "name": "Pune", "lat": 18.52, "lon": 73.85}

    def test_item_conversion(self):
        from tripplanner.places import otm_item_to_poi
        poi = otm_item_to_poi(OTM_ITEM)
        self.assertEqual((poi.id, poi.name, poi.mainstream), ("otm_N1", "Shaniwar Wada", True))
        self.assertIn("heritage", poi.tags)
        self.assertEqual((poi.open_min, poi.close_min), (540, 1080))

    def test_low_rate_is_non_mainstream_and_geojson_shape_works(self):
        from tripplanner.places import otm_item_to_poi
        poi = otm_item_to_poi({"geometry": {"coordinates": [73.8, 18.5]},
                               "properties": {"xid": "G1", "name": "Small Temple", "kinds": "religion,hindu_temples", "rate": "1"}})
        self.assertFalse(poi.mainstream)
        self.assertEqual(poi.cost, 0)

    def test_unusable_items_skipped(self):
        from tripplanner.places import otm_item_to_poi
        self.assertIsNone(otm_item_to_poi({**OTM_ITEM, "name": ""}))
        self.assertIsNone(otm_item_to_poi({**OTM_ITEM, "kinds": "interesting_places,other"}))
        self.assertIsNone(otm_item_to_poi({"xid": "Z", "name": "No coords", "kinds": "historic"}))

    def test_search_sends_key_and_one_request_per_category(self):
        from tripplanner.places import OpenTripMapClient
        fake = FakeFetch({"https://api.opentripmap.com": lambda body: self.GEO if False else None})
        calls = []

        def route(url):
            return self.GEO if "geoname" in url else otm_items(3)
        fake = lambda method, url, headers=None, body=None, **kw: (calls.append(url), route(url))[1]
        pois = OpenTripMapClient("KEY123", fake).search("Pune", ["heritage", "food"], 1)
        self.assertEqual(len(pois), 3)                       # same 3 ids from both categories, de-duplicated
        self.assertEqual(len(calls), 3)                      # geoname + two radius requests
        self.assertTrue(all("apikey=KEY123" in u for u in calls))
        self.assertIn("kinds=foods", calls[2])

    def test_unknown_city_raises(self):
        from tripplanner.places import OpenTripMapClient
        fake = lambda method, url, headers=None, body=None, **kw: {"status": "NOT_FOUND"}
        with self.assertRaises(CloudServiceError):
            OpenTripMapClient("K", fake).search("Nowhereville", [], 1)

    def test_not_found_status_is_respected_even_if_coordinates_present(self):
        from tripplanner.places import OpenTripMapClient
        fake = lambda method, url, headers=None, body=None, **kw: {"status": "NOT_FOUND", "lat": 0, "lon": 0}
        with self.assertRaises(CloudServiceError):
            OpenTripMapClient("K", fake).search("Atlantis", [], 1)

    def test_missing_key_rejected(self):
        from tripplanner.places import OpenTripMapClient
        with self.assertRaises(CloudServiceError):
            OpenTripMapClient("")

    def test_api_key_not_leaked_in_error_message(self):
        import urllib.error

        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "x", {}, None)
        with self.assertRaises(CloudServiceError) as cm:
            request_json("GET", "https://api.opentripmap.com/0.1/en/places/geoname?name=Pune&apikey=SUPERSECRET",
                         _open=opener, _sleep=lambda s: None)
        self.assertNotIn("SUPERSECRET", str(cm.exception))


class ProviderChainTests(unittest.TestCase):
    def setUp(self):
        sources._cache = MemoryCache()
        self.pune = TripBrief("pune", 2, 3000, ["heritage"], "moderate")
        self.geo = {"status": "OK", "lat": 18.52, "lon": 73.85}

    def fetch(self, otm=None, osm=None, google=None):
        routes = {}
        routes["https://api.opentripmap.com"] = otm if otm is not None else CloudServiceError("otm down")
        routes["https://nominatim.openstreetmap.org"] = [{"lat": "18.5", "lon": "73.8"}] if osm is not None else CloudServiceError("osm down")
        if osm is not None:
            routes["https://overpass-api.de"] = osm
        routes["https://places.googleapis.com"] = google if google is not None else CloudServiceError("g down")
        calls = []

        def f(method, url, headers=None, body=None, **kw):
            calls.append(url)
            for prefix, reply in routes.items():
                if url.startswith(prefix):
                    if isinstance(reply, Exception):
                        raise reply
                    return reply(url) if callable(reply) else reply
            raise AssertionError(url)
        f.calls = calls
        return f

    @staticmethod
    def otm_reply(url):
        return {"status": "OK", "lat": 18.52, "lon": 73.85} if "geoname" in url else otm_items(10)

    def test_opentripmap_used_when_keyed(self):
        f = self.fetch(otm=self.otm_reply)
        pois, src = sources.get_pois(self.pune, Settings(opentripmap_key="k"), "live", f)
        self.assertEqual((src, len(pois)), ("OpenTripMap", 10))
        self.assertFalse(any("overpass" in u for u in f.calls))       # never needed the OSM servers

    def test_falls_back_to_opentripmap_when_openstreetmap_is_down(self):
        f = self.fetch(otm=self.otm_reply)                              # OSM routes are down in this fake
        self.assertEqual(sources.get_pois(self.pune, Settings(opentripmap_key="k"), "live", f)[1], "OpenTripMap")

    def test_google_failure_falls_through_to_opentripmap(self):
        f = self.fetch(otm=self.otm_reply)
        pois, src = sources.get_pois(self.pune, Settings(google_key="g", opentripmap_key="k"), "live", f)
        self.assertEqual(src, "OpenTripMap")

    def test_thin_opentripmap_result_falls_through_to_openstreetmap(self):
        thin = lambda url: {"status": "OK", "lat": 18.5, "lon": 73.8} if "geoname" in url else otm_items(2)
        els = {"elements": [{"type": "node", "id": i, "lat": 18.5, "lon": 73.8,
                             "tags": {"name": f"P{i}", "historic": "fort"}} for i in range(9)]}
        f = self.fetch(otm=thin, osm=els)
        self.assertEqual(sources.get_pois(self.pune, Settings(opentripmap_key="k"), "live", f)[1], "OpenStreetMap")

    def test_everything_down_for_unknown_city_gives_friendly_error(self):
        with self.assertRaises(Exception) as cm:
            sources.get_pois(self.pune, Settings(opentripmap_key="k"), "live", self.fetch())
        self.assertIn("No place data", str(cm.exception))

    def test_settings_read_opentripmap_key(self):
        self.assertEqual(Settings.from_env({"OPENTRIPMAP_API_KEY": "abc"}).opentripmap_key, "abc")

    def test_plan_end_to_end_with_opentripmap_only(self):
        f = self.fetch(otm=self.otm_reply)
        result = run_plan("2 days in Pune, budget 3000 per day, heritage", Settings(opentripmap_key="k"),
                          places_mode="live", fetch=f)
        self.assertEqual(result.places_source, "OpenTripMap")
        self.assertGreater(result.itinerary.stop_count, 0)


class ExplainerTests(unittest.TestCase):
    def setUp(self):
        self.result = run_plan("2 days in Jaipur, budget 3000 per day, heritage", Settings())
        self.day = self.result.itinerary.days[0]
        self.brief = self.result.itinerary.brief

    def test_llm_prompt_contains_only_planned_places(self):
        seen = {}
        def complete(prompt, system=None):
            seen["p"], seen["s"] = prompt, system
            return "Nice day."
        LLMExplainer(complete).explain(self.brief, self.day)
        for s in self.day.stops:
            self.assertIn(s.poi.name, seen["p"])
        self.assertIn("ONLY", seen["s"])

    def test_reply_is_length_capped(self):
        self.assertEqual(len(LLMExplainer(lambda p, system=None: "x" * 5000, max_chars=100).explain(self.brief, self.day)), 100)

    def test_failure_falls_back_to_template(self):
        def boom(p, system=None):
            raise CloudServiceError("down")
        fb = FallbackExplainer(LLMExplainer(boom))
        self.assertIn("stops between", fb.explain(self.brief, self.day))
        self.assertTrue(fb.used_fallback)


class HttpTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        import urllib.error
        calls = {"n": 0}

        def opener(req, timeout):
            calls["n"] += 1
            if calls["n"] < 3:   # a rate limit (429) first, then a server error (503), then success
                raise urllib.error.HTTPError(req.full_url, 429 if calls["n"] == 1 else 503, "x", {}, None)
            return io.BytesIO(b'{"ok": true}')
        opener_cm = lambda req, timeout: contextlib_wrap(opener(req, timeout))
        self.assertEqual(request_json("GET", "https://x.test/a", _open=opener_cm, _sleep=lambda s: None), {"ok": True})
        self.assertEqual(calls["n"], 3)

    def test_gives_up_and_hides_headers(self):
        import urllib.error

        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 429, "x", {}, None)
        with self.assertRaises(CloudServiceError) as cm:
            request_json("POST", "https://x.test/a", {"x-api-key": "TOPSECRET"}, {"a": 1},
                         _open=opener, _sleep=lambda s: None)
        self.assertNotIn("TOPSECRET", str(cm.exception))

    def test_client_error_is_not_retried(self):
        import urllib.error
        calls = {"n": 0}

        def opener(req, timeout):
            calls["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 403, "x", {}, None)
        with self.assertRaises(CloudServiceError):
            request_json("GET", "https://x.test/a", _open=opener, _sleep=lambda s: None)
        self.assertEqual(calls["n"], 1)


class contextlib_wrap:
    def __init__(self, inner): self.inner = inner
    def __enter__(self): return self.inner
    def __exit__(self, *a): return False


class HeaderTests(unittest.TestCase):
    def test_identifies_itself_and_keeps_caller_headers(self):
        seen = {}

        def opener(req, timeout):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return contextlib_wrap(io.BytesIO(b"{}"))
        request_json("POST", "https://x.test/a", {"x-api-key": "K"}, {"a": 1}, _open=opener)
        self.assertIn("ai-trip-planner", seen["user-agent"])
        self.assertEqual(seen["x-api-key"], "K")
        self.assertEqual(seen["content-type"], "application/json")


class DotenvTests(unittest.TestCase):
    def test_reads_file_without_overriding_real_env(self):
        import os, tempfile
        from tripplanner.config import load_dotenv
        path = os.path.join(tempfile.mkdtemp(), ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('# comment\nANTHROPIC_API_KEY="from-file"\nGOOGLE_PLACES_API_KEY=g-file\nPORT=\n  BAD LINE\n')
        env = {"GOOGLE_PLACES_API_KEY": "from-shell"}
        load_dotenv(path, env)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "from-file")
        self.assertEqual(env["GOOGLE_PLACES_API_KEY"], "from-shell")
        self.assertNotIn("PORT", env)

    def test_missing_file_is_fine(self):
        from tripplanner.config import load_dotenv
        load_dotenv("/no/such/.env", {})


class SettingsTests(unittest.TestCase):
    def test_from_env(self):
        s = Settings.from_env({"ANTHROPIC_API_KEY": "a", "GOOGLE_PLACES_API_KEY": "g", "PORT": "9000"})
        self.assertEqual((s.anthropic_key, s.google_key, s.port, s.llm_model), ("a", "g", 9000, "claude-sonnet-5-5"))

    def test_empty_values_become_none(self):
        self.assertIsNone(Settings.from_env({"ANTHROPIC_API_KEY": ""}).anthropic_key)


if __name__ == "__main__":
    unittest.main()

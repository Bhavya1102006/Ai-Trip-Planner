# Cloud setup guide

The planner now has four optional cloud parts. Each one is switched on by a key or a flag and
falls back to the offline behaviour if it is missing or fails, so the demo never breaks.

| Part | Switch | Falls back to |
|---|---|---|
| Language model (parses messy briefs, writes day summaries) | `GROQ_API_KEY` or `ANTHROPIC_API_KEY`, plus `--llm` / `"llm": true` | rule-based parser, template text |
| Live places | `--live` / `"live": true`; tries Google (if keyed), then OpenTripMap (if keyed), then OpenStreetMap | bundled Jaipur file (Jaipur only) |
| Web API | `python -m tripplanner.server` or gunicorn | n/a |
| Database | `DATABASE_URL` (SQLite by default, PostgreSQL in the cloud) | n/a |

## 1. Get the keys
- **Groq** (free tier available): create a key at console.groq.com/keys and put it in `GROQ_API_KEY`. Groq changes its model list now and then; `python -m scripts.check_cloud` prints the models your key can use if the default stops working, and you set one with `GROQ_MODEL`.
- **Anthropic** (alternative): create a key in the Anthropic Console. The model name is read from `LLM_MODEL`
  (default `claude-sonnet-5-5`); change it there if you want another model.
- **Google Places**: in Google Cloud, create a project, enable **Places API (New)**, turn on billing,
  create an API key and restrict it to that API. Text Search requests that ask for opening hours
  and price level are billed at a higher tier, so keep usage low while testing.
- **OpenTripMap** (free, optional backup for live places): sign up at dev.opentripmap.org and copy the key into `OPENTRIPMAP_API_KEY`. Places are tried in this order: Google (if keyed), OpenTripMap (if keyed), OpenStreetMap. Its data comes from OpenStreetMap and others under the ODbL licence, so credit OpenStreetMap contributors wherever you show results. The free tier has request limits; check your dashboard.
- **Database**: create a free PostgreSQL database (Render, Supabase or Neon) and copy its connection string.

Never commit keys. Copy `.env.example` to `.env` for local use, and enter them in the host's
dashboard when deploying. If a key is ever pasted into chat, a commit or a screenshot, revoke it and make a new one.

## 2. Check them (do this first)
    export ANTHROPIC_API_KEY=... GOOGLE_PLACES_API_KEY=... DATABASE_URL=postgresql://...
    pip install -r requirements.txt
    python -m scripts.check_cloud
It prints PASS/FAIL per service. The Google and PostgreSQL integrations were written against the
documentation and tested with simulated responses only, so this script is the real test. If the
Google check reports missing fields, send me its output and I will adjust the field mapping.

## 3. Run it
    python -m tripplanner "3 days in Pune, budget 3000 per day, food and nature" --live --llm
    python -m tripplanner.server            # dev server on http://localhost:8000

    curl -X POST localhost:8000/plan -H "Authorization: Bearer $API_AUTH_TOKEN" \
         -d '{"brief":"3 days in Jaipur, 3000 a day, forts and food","user_id":"alice","save":true,"llm":true,"live":true}'
    curl "localhost:8000/trips?user_id=alice" -H "Authorization: Bearer $API_AUTH_TOKEN"

## 4. Deploy the API (Render)
1. Push this folder to GitHub.
2. In Render: New > Blueprint (uses `render.yaml`) or New > Web Service with
   build `pip install -r requirements.txt` and start
   `gunicorn tripplanner.server:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60`.
3. Add the environment variables from `.env.example` in the dashboard. Set `API_AUTH_TOKEN` to a long random value.
4. Open `/health` on your service URL. It reports which services are configured and never shows keys.

A front end hosted on Vercel (or anywhere else) should call this API; set `ALLOWED_ORIGIN` to the
front end's address so the browser allows it. Keep `API_AUTH_TOKEN` on a server side of the front
end, because anything shipped to a browser can be read by users.

## 5. Things to know
- **`user_id` is a label, not a login.** Anyone who knows it can read that user's trips. For real
  accounts, add an auth provider (Supabase Auth, Firebase, OAuth) before storing anything private.
- **Google data is not stored.** Google's terms restrict saving Places content, so results are kept in memory
  for an hour and only the generated plan is saved to the database. Check the current terms before changing this.
- **Opening hours** use one weekday (tomorrow by default), not each day of a multi-day trip yet.
- **Prices** from Google's price level are mapped to rupee estimates in `places.py`; adjust for other currencies.
- **The model never picks the places.** It only reads the brief and phrases a schedule the solver already fixed; its
  output is validated like any other input and any failure falls back to offline text.

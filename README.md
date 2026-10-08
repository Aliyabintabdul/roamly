# Roamly

Personal travel itinerary and budget planner — a warm travel journal with day-by-day timelines.

A personal full-stack portfolio MVP: plain HTML/CSS/JavaScript frontend, Python standard-library backend and SQLite. It runs independently with its own database. All sample data is fictional; there are no real users, clients or production deployments.

## Run it
Requires Python 3.10+ only — no pip packages, no npm, no CDN. From this folder:

```
py server.py --port 8003               # Windows
python3 server.py --port 8003          # macOS / Linux
py server.py --port 8003 --seed-demo   # optional: add the fictional demo account
```
Open http://localhost:8003 and stop with Ctrl+C. The database `data.sqlite3` is created on first launch (override with `DB_PATH`). To run all five projects at once use `start-all.bat` / `stop-all.bat` in the parent folder.

**Demo access (optional, local only):** `--seed-demo` creates `demo@example.com` / `DemoPass123!` (override with `DEMO_PASSWORD`) filled with clearly fictional sample data, marked in the UI with a "Demo workspace" banner. Seeding is refused when `APP_ENV=production`, and never overwrites an existing demo account. Any visitor can also register a fresh, empty, private account.

## Tests
```
py -m unittest discover -s tests
```
Integration tests start the real server on a free port with a throwaway database and exercise it over HTTP: registration/login/logout, session invalidation after logout, CSRF and origin rejection, login rate limiting, account isolation, the domain rules below, CSV formula neutralisation and persistence across a restart.

## Features
- **Trips**: name, destination, country, start/end dates, trip currency, optional planned budget, a destination time-zone label, notes. Create/edit/delete (confirmation lists linked activities and expenses).
- **Trip postcards** with phase (upcoming / happening now / past), filter chips, search and CSV export.
- **Day-by-day itinerary** timeline: every day of the trip is listed, with activities ordered by local time — time range, location, category, estimated cost, booking status and booking reference. Add directly to any day.
- **Budget & expenses**: planned budget, estimated cost (sum of activity estimates) and actual spending (recorded expenses) are shown **separately**, plus remaining budget, spending by category and estimates by booking status. Expenses can be linked to an activity.
- **Itinerary CSV export** per trip (activities + expenses). Illustrations are inline SVG — no remote images.

## Domain rules (enforced on the server)
- **Scheduling is explicit**: activity dates/times are stored exactly as entered and mean local time at the destination; no time-zone conversion happens. Activities must fall within the trip's dates and end after they start. Trip dates can't be changed so that existing activities fall outside them.
- **Currency is explicit**: every amount on a trip (budget, estimates, expenses) is in the trip's currency. The currency is locked once amounts are recorded, because Roamly does not convert currencies.
- Expenses must be positive and may only link to an activity on the same trip; deleting an activity keeps its expenses but unlinks them.
- Maximum trip length 90 days.

## Data model
```
users ─< trips ─┬─< activities
                └─< expenses (activity_id → activities, nullable)
```
`trips(name, destination, country, start_date, end_date, currency, budget, timezone, notes)` · `activities(trip_id, day, start_time, end_time, title, location, category, est_cost, booking_status, booking_ref, notes)` · `expenses(trip_id, activity_id, spent_on, category, description, amount, notes)`

## API
JSON over HTTP; authenticated with the session cookie plus `X-CSRF-Token` on mutations. Auth: `POST /api/register|login|logout`, `GET /api/session`, `GET /api/health`, `GET /api/meta`.
`GET /api/dashboard` · `GET|POST /api/trips` (`q, when`) · `GET|PUT|DELETE /api/trips/:id` · `POST /api/trips/:id/activities` · `PUT|DELETE /api/activities/:id` · `POST /api/trips/:id/expenses` · `PUT|DELETE /api/expenses/:id` · `GET /api/export/trips` · `GET /api/export/trips/:id/itinerary`

## Project structure
`server.py` domain schema, migrations, validation and routes · `core.py` shared foundation (identical in all five projects) · `frontend/` `index.html`, `style.css`, `app.js` (this product's UI) and `lib.js` (shared helpers) · `tests/` integration tests.

## Security & reliability (shared foundation in `core.py`)
- Passwords hashed with PBKDF2-HMAC-SHA256 (200,000 iterations, per-user salt); constant-time comparison.
- Sessions: random 256-bit token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` in production); only a SHA-256 hash is stored. Sessions expire after 8 hours (`SESSION_TTL_SECONDS`) and are deleted on logout, so a copied cookie stops working.
- CSRF: every POST/PUT/DELETE needs the per-session `X-CSRF-Token` header and a same-origin `Origin`; JSON-only request bodies.
- Login rate limiting: 5 failed attempts per email+IP and 30 per IP per 15 minutes (HTTP 429). Registration: 10 accounts per IP per hour.
- Account isolation: every query is scoped to the signed-in user's id; other users' IDs return 404.
- Server-side validation with field-level messages; parameterised SQL everywhere; each mutating request runs in one `BEGIN IMMEDIATE` transaction (all related writes succeed or none do).
- Consistent errors: `{"error": "...", "code": "...", "fields": {...}}` with appropriate HTTP status codes.
- CSV exports neutralise spreadsheet formulas (cells starting with `= + - @ |`, tab or CR are prefixed with `'`).
- Static files are served with a strict Content-Security-Policy (no inline scripts), `nosniff`, `DENY` framing and path-traversal protection.
- Schema migrations are versioned (`PRAGMA user_version`); an existing database is copied to `data.backup-v<N>-<timestamp>.sqlite3` before migrating. Data from the original single-table version (`records`) is migrated into the new tables and the old table is kept as `legacy_records`.

## Limitations
Roamly never books flights, hotels or activities — booking status is your own note. No maps, weather, exchange rates or offline sync.
Other: single-user accounts, no password reset, no pagination beyond sensible limits, English UI only, dates displayed in the browser's locale. Visual design was checked in a desktop browser and at mobile width, but not across every browser.

## Production deployment requirements
This is a local portfolio MVP. Before exposing it publicly you would need at least:
- HTTPS termination (reverse proxy such as Caddy/Nginx) and `APP_ENV=production` (enables `Secure` cookies and disables demo seeding).
- A production WSGI/ASGI-grade server or a hardened proxy in front of `ThreadingHTTPServer`, request timeouts and process supervision (systemd, a container restart policy, etc.).
- Persistent storage with scheduled backups for the SQLite file (or migration to PostgreSQL for concurrent multi-instance use).
- Account recovery (password reset by email), email verification and optionally MFA — none are implemented.
- Centralised logging/monitoring, and a shared rate-limit store if running more than one instance.
- Review of privacy/data-retention obligations for the data you store.

# QR Attendance on Cloudflare (Python Worker + D1)

This folder is the same app as `../python`, rebuilt to run on Cloudflare:

- A Python Worker serves every page.
- D1 (Cloudflare's SQLite database) stores the data.
- The Worker's static assets serve the CSS, logo and scanner script, so you do not need a separate Pages project.

The pages, accounts and features are the same as the Flask version. Three things are different:

- The staff page scans with the browser's own QR reader (`BarcodeDetector`). Chrome, Edge and Android browsers have it. Safari and Firefox fall back to pasting the code.
- Times use a fixed UTC offset, set by `TZ_OFFSET_HOURS` (8 for the Philippines).
- Everything is async and goes through D1, so there is no MySQL or SQLite file.

## What you need

- A Cloudflare account (the free one is enough to start).
- Node 20 or newer.
- `uv` version 0.13.4 or newer (`pip install -U uv` or see docs.astral.sh/uv).
- Python 3.12 or newer.

## First deploy

Run these from this `worker` folder, one line at a time.

1. Log in to Cloudflare.

       npx wrangler login

2. Create the database.

       npx wrangler d1 create qr-attendance

   Copy the `database_id` it prints into `wrangler.jsonc`, replacing `PASTE-YOUR-D1-DATABASE-ID-HERE`.

3. Create the tables and the five starter rooms in the live database.

       npx wrangler d1 execute qr-attendance --remote --file=schema.sql

4. Deploy.

       uv run pywrangler deploy

   Wrangler prints your address, like `https://qr-attendance.YOUR-NAME.workers.dev`.

5. Open the address and log in. The first request creates the three starter accounts automatically:

   | Role | Email | Password |
   |---|---|---|
   | Administrator | admin124@attendance.ph | Admin@12345 |
   | Teacher | teacher@attendance.ph | Teacher@12345 |
   | Staff | staff@attendance.ph | Staff@12345 |

   Change these passwords from the Profile page right away.

## Running it on your computer first

    npx wrangler d1 execute qr-attendance --local --file=schema.sql
    uv run pywrangler dev

Open http://localhost:8787. The local database lives in `.wrangler/` and is separate from the live one.

If you only want to look at the pages without Cloudflare tooling, this runs the same code against a throwaway in-memory database:

    pip install jinja2 segno
    python tools/preview.py

Then open http://localhost:8090. Data resets every time you stop it.

## Settings

Set these under `vars` in `wrangler.jsonc`.

| Setting | What it does | Default |
|---|---|---|
| `TZ_OFFSET_HOURS` | Hours ahead of UTC for displayed times | 8 |
| `DEMO_LOGIN` | `true` shows one-click demo logins. Never on a live site | false |

## After you edit a template

The Worker cannot read files at run time, so the HTML templates are bundled into `src/templates_bundle.py`. After changing anything in `templates/`, run:

    python tools/build_templates.py

then deploy again. The CSS and images in `public/` need no build step.

## Tests

    pip install pytest pytest-asyncio jinja2 segno
    python -m pytest

The tests run the real app code against an SQLite stand-in for D1. They cover login, sign-up, the full QR flow, roles, admin pages, filters, CSV and the database layer.

## Honest limits

- This was tested against the D1 stand-in and a browser preview, not on Cloudflare's own Python runtime, which could not be reached from where it was built. If `pywrangler deploy` or the first page load shows an error, run `npx wrangler tail` and read the message. The most likely trouble spots are the package install step and the password hashing call.
- Password hashing uses the Worker's built-in WebCrypto, which keeps it fast. Python code on the free plan has a small CPU allowance per request, so if pages fail with a CPU limit error, move the Worker to the Workers Paid plan.
- D1 has no multi-step transactions. Steps that must succeed together (time in, time out, password reset) use D1 batches, which are atomic.
- Passwords are stored as `salt:hash` with PBKDF2-SHA256 at 100000 rounds, the same format as the Java and Flask versions.
# Attendance-qr-Workers

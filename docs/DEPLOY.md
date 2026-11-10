# Deploying Signal Desk

Two things differ from a local run: the app is served as a serverless function,
and the trade journal needs a real database.

## 1. Push to GitHub

The repository already exists locally with one commit on `main`. From the
project root:

```powershell
gh auth login                                    # GitHub.com -> HTTPS -> browser
gh repo create signal-desk --public --source=. --push
```

Without the GitHub CLI:

```powershell
# create the empty repo at github.com/new named "signal-desk", then:
git remote add origin https://github.com/krank21r/signal-desk.git
git push -u origin main
```

`.gitignore` already keeps `data/*.db`, the market cache, logs and the
`_langalpha/` reference clone out of the repository.

## 2. Create the journal database

The journal is the only stateful part. On Vercel the filesystem is read-only
apart from `/tmp`, and `/tmp` is discarded when an instance is recycled, so
SQLite cannot hold your trades there.

Either works — both give a Postgres URL:

- **Neon through Vercel** — Marketplace → Neon → install, create a database, then connect it to
  the project. No separate Neon account needed. This is the current path: Vercel's own Postgres
  offering is now only for accounts that already had one, and new stores go through the Neon
  integration.
- **Neon directly** — free tier at neon.tech, no card, then paste the URL into Vercel by hand.

Copy the **pooled** connection string and keep `sslmode=require`:

```
postgresql://user:password@host/dbname?sslmode=require
```

Name the variable `DATABASE_URL`. The Neon Vercel integration sometimes injects `POSTGRES_URL`
instead; the app reads whichever exists, so either is fine as long as one is set.

## 3. Verify the database before deploying

```powershell
$env:DATABASE_URL = "postgresql://...?sslmode=require"
python scripts/check_postgres.py
```

It creates the schema, inserts a trade, re-inserts the same one to prove it does
not duplicate, lists, closes, deletes, and prints `RESULT: PASS`. Nothing is left
behind.

## 4. Import into Vercel

1. Vercel → **Add New → Project → Import Git Repository** → pick `signal-desk`.
2. Framework preset: **Other**. `vercel.json` already routes everything to the
   FastAPI app in `api/index.py` and sets a 300s function duration.
3. **Environment Variables** → add `DATABASE_URL` with the string from step 2.
   Set it for Production, Preview and Development.
4. Deploy.

Every later `git push` redeploys automatically.

## 5. Check the deployment

```powershell
curlhttps://<project>.vercel.app/api/health
# {"status":"ok","universe":20}

curl https://<project>.vercel.app/api/trades
# {"trades":[],...,"storage":{"persistent":true,"path":"/tmp/signal-desk"}}
```

`storage.persistent` must be `true` — that is how the app reports that the
journal is going to Postgres rather than to `/tmp`.

## What behaves differently in production

| | local | Vercel |
| --- | --- | --- |
| Journal | SQLite at `data/trades.db` | Postgres via `DATABASE_URL` |
| Bar cache | SQLite, persists | `/tmp`, lost on cold start |
| First request after idle | fast (warm) | 10–30s while 20 symbols are fetched |
| Background warming | runs at startup | skipped (`VERCEL` is set) |

If the cold-start latency bothers you, the cache needs a shared store as well —
Upstash Redis or the same Postgres — which is a separate change.

## Other hosts

Any host with a real disk works unchanged: SQLite is the default and nothing
else is required. Set `SIGNAL_DESK_DATA_DIR` if the writable path is not
`./data`, and start it with:

```
python -m uvicorn --app-dir . app.main:app --host 0.0.0.0 --port $PORT
```

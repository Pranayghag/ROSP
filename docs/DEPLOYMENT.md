# Deploying CampusCare to Vercel

Everything in the repository is ready. What remains needs your logins, so the
steps below are yours to run — they take about fifteen minutes.

Deploy from the account **amanguptawork2726@gmail.com**.

---

## Why two extra services are needed

Vercel runs your code as short-lived serverless functions. Two consequences
drive the whole setup:

**The filesystem is ephemeral.** Only `/tmp` is writable, and it is wiped
between invocations. A photo written to disk during an upload would be gone by
the time anyone opened the complaint. So evidence goes to **Cloudinary**
instead, and `api/index.py` refuses to boot on Vercel unless that is
configured — better a loud failure at deploy time than photos quietly
disappearing in front of a class.

**There is no database.** Vercel hosts none, and the MySQL in your XAMPP
install is on your laptop, unreachable from the internet. So a hosted
**Postgres** is needed. `config.py` rewrites the provider's URL for SQLAlchemy,
so no code changes.

Both have free tiers that comfortably fit a college project.

---

## Step 1 — Cloudinary (evidence photos)

1. Sign up at <https://cloudinary.com/users/register_free>
2. On the dashboard, copy three values from **Account Details**:
   - Cloud name
   - API Key
   - API Secret

Free tier is 25 GB storage and 25 GB monthly bandwidth — at roughly 200 KB per
photo, that is tens of thousands of images.

> Photos are uploaded with Cloudinary's `authenticated` delivery type, so they
> have **no public URL**. `/evidence/<id>` still checks permissions on every
> request and fetches the bytes server-side, exactly as it does locally.

## Step 2 — Neon (database)

1. Sign up at <https://neon.tech> and create a project
2. Copy the connection string — it looks like
   `postgres://user:password@ep-xxx.aws.neon.tech/neondb`

Vercel Postgres works identically if you prefer to stay in one dashboard.

## Step 3 — Import the repository

1. Go to <https://vercel.com/new>
2. Sign in with **amanguptawork2726@gmail.com** and connect GitHub
3. Import `AmanGupta910/ROSP`
4. Leave the build settings alone — `vercel.json` handles them

## Step 4 — Environment variables

In **Settings → Environment Variables**, add these before the first deploy.
Generate the secret key locally first:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

| Variable | Value |
| --- | --- |
| `SECRET_KEY` | the value you just generated |
| `FLASK_ENV` | `production` |
| `DATABASE_URL` | the Neon connection string |
| `STORAGE_BACKEND` | `cloudinary` |
| `CLOUDINARY_CLOUD_NAME` | from step 1 |
| `CLOUDINARY_API_KEY` | from step 1 |
| `CLOUDINARY_API_SECRET` | from step 1 |
| `SESSION_COOKIE_SECURE` | `true` |
| `BASE_URL` | `https://your-project.vercel.app` |
| `ADMIN_EMAIL` | `guptavipul2726@gmail.com` |

Optional, if you want email working in production:

| Variable | Value |
| --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | a Gmail **App Password**, not your account password |
| `EMAIL_FROM` | `CampusCare <your@gmail.com>` |

`BASE_URL` is a chicken-and-egg: deploy once, copy the URL Vercel assigns, set
it, redeploy. Only email links depend on it, so nothing breaks meanwhile.

## Step 5 — Deploy, then create the tables

Click **Deploy**. When it finishes, create the schema against Neon from your
own machine — the same `DATABASE_URL`, run locally:

```bash
cd "C:/Users/abc/OneDrive/Desktop/ROSP" && DATABASE_URL="paste-neon-url-here" .venv/Scripts/python.exe scripts/migrate.py --apply
```

Then the reference data and your administrator account:

```bash
cd "C:/Users/abc/OneDrive/Desktop/ROSP" && DATABASE_URL="paste-neon-url-here" .venv/Scripts/python.exe scripts/seed.py
```

```bash
cd "C:/Users/abc/OneDrive/Desktop/ROSP" && DATABASE_URL="paste-neon-url-here" .venv/Scripts/python.exe scripts/create_admin.py --email guptavipul2726@gmail.com --name "Vipul Gupta"
```

That last command prints a one-time link for setting the password. Open it
against the **deployed** site, not localhost — swap the host in the URL.

---

## Checking it worked

1. Open your Vercel URL — the CampusCare landing page should appear
2. Register a student account
3. File a complaint **with a photo**
4. Open the complaint and confirm the photo displays
5. Redeploy from the Vercel dashboard, then look at that complaint again

Step 5 is the one that matters. If the photo still displays after a redeploy,
object storage is working. If it 404s, `STORAGE_BACKEND` is not set to
`cloudinary` and photos are going to a disk that no longer exists.

---

## What stays local

Your XAMPP MySQL and `.env` are untouched. Locally the app still uses MySQL and
the local filesystem — `STORAGE_BACKEND` defaults to `local`, so development
behaves exactly as before. The two setups share code, not configuration.

---

## Known limits on the free tier

**Cold starts.** An idle function takes a second or two to wake. Normal for
serverless; the first page load after a quiet spell feels slow.

**No background work.** SLA escalation runs when an admin presses *Escalate
overdue* rather than on a timer. Vercel Cron can automate it on a paid plan.

**Synchronous email.** A slow SMTP server delays the request. Vercel's function
timeout is 10 seconds on the free tier, and `SMTP_TIMEOUT` defaults to 20 —
lower it to `8` in production, or a slow send could time out the whole request.

**Neon idles.** The free tier sleeps after inactivity; the first query wakes it,
adding a second or so. `pool_pre_ping` is already enabled, so a stale
connection is retried rather than erroring.

---

## If something breaks

**500 on every page** — usually `DATABASE_URL` missing or wrong. Check
Vercel's function logs (Deployments → the deployment → Functions).

**Deploy fails with "STORAGE_BACKEND must be 'cloudinary'"** — that guard is
working as intended. Set the variable and redeploy.

**Photos upload but do not display** — Cloudinary credentials are wrong. Check
`/admin/email-log`, which records storage failures too.

**"relation does not exist"** — step 5 was skipped; run the migration.

# AfyaTrack PHC 4.8 — connected multi-unit starter

This package upgrades the supplied 4.7 HTML interface with a server login and a tenant-scoped server data store.

## What is connected
- The existing 4.7 dashboard and its Patients, Clinical Records, Laboratory, Pharmacy, Appointments, Staff, and Backup/Restore screens remain in the interface.
- Sign-in uses server-side Flask sessions and password hashes.
- Each normal user is assigned to one PHC unit.
- Each unit's complete app dataset is stored in a separate `UnitData` row keyed by `unit_id`; API access derives the unit from the authenticated account, never from a browser-supplied unit ID.
- Browser localStorage remains only as a cache; the authenticated server is the source of truth.
- Platform administrator can create units/users using protected admin API endpoints and select a unit workspace.

## Important safety and readiness notice
This is an implementation starter, not a certified electronic medical record or a completed clinical system. The current data model stores the unit's app records as one JSON document per unit, which is convenient for connecting the existing app but is not the ideal normalized model for a long-term clinical database. It does not yet include field-level permissions, full audit trails, MFA, password reset, comprehensive rate limiting, formal backup automation, disaster recovery testing, or independent security/compliance review. Do not put identifiable real patient data into it until those items and local legal/privacy requirements have been reviewed and implemented. Use test data only during setup.

The platform administrator's unit selection is available through `/admin/select-unit`; create units/users via the CLI or the protected API. A platform admin must select a unit before using the data API. Unit users cannot switch units.

## Run locally
Requires Python 3.11+.

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
flask --app app init-db
flask --app app create-platform-admin
flask --app app create-unit
flask --app app create-unit-user
flask --app app run --host 0.0.0.0 --port 8000
```

On Windows PowerShell, set the secret with:
`$env:SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"`

Open `http://127.0.0.1:8000`. For testing only, SQLite is used if DATABASE_URL is omitted.

## Docker / hosted deployment
1. Use a Linux VPS or managed container host; do not host the backend on the Android phone.
2. Point a domain to the server and configure a reverse proxy with a valid HTTPS certificate.
3. Copy `.env.example` to `.env`, generate a unique SECRET_KEY, and set a strong POSTGRES_PASSWORD.
4. Run `docker compose up --build -d`.
5. Initialize database and create the first administrator:
   `docker compose exec web flask --app app init-db`
   `docker compose exec web flask --app app create-platform-admin`
6. Use the administrator to create units and accounts. The web interface can be opened from Android Chrome.

Do not expose the database port publicly. Restrict server administration, keep OS/packages updated, and configure encrypted off-site backups and restore drills. In production, terminate HTTPS at a trusted reverse proxy and keep COOKIE_SECURE=1.

## Admin API
All endpoints require a platform-admin session:
- `GET /admin/units`
- `POST /admin/units` JSON: `{"name":"Banganje PHC","code":"BANGANJE"}`
- `POST /admin/users` JSON: `{"username":"banganje_admin","password":"use-a-long-unique-password","role":"unit_admin","unit_id":1}`
- `POST /admin/select-unit` JSON: `{"unit_id":1}`

For API use, sign in first in the same browser session. Never share admin credentials.

## Migration from 4.7
1. In 4.7, open Backup & Restore and export a JSON backup.
2. Keep an untouched copy of the export in a safe location.
3. Create the target unit and its unit account in 4.8.
4. Sign in as the target unit account in 4.8.
5. Sign in to the target unit account, open **Backup & Restore**, choose the 4.7 JSON backup, and use **Restore**. The existing 4.7 backup format is accepted by the retained restore screen; after confirmation, the updated dataset is saved to that unit's server workspace.
6. Restore replaces the target unit's current dataset. Export a backup of the target unit first if it already contains data. Do not upload the same backup more than once; verify record counts using test copies before any live migration.

## API
- `GET /api/me`
- `GET /api/data`
- `PUT /api/data`

The browser syncs the complete dataset after edits. Avoid simultaneous editing from multiple devices in this initial version: last save wins. A future version should move records into normalized tables with optimistic concurrency and per-record audit events.

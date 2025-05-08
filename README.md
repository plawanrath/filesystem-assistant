# Setup

```
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

Create a .env file in top-level and add credentials
```
# ── ChatGPT ─────────────────────────────────────────
OPENAI_API_KEY="key"

# Path to the Google OAuth client-secrets JSON you downloaded
GOOGLE_CLIENT_SECRET_JSON=<credentials file path>

# ── Synology NAS (if needed)  ─────────
NAS_HOST=
NAS_PORT=
NAS_USER=
NAS_PASS=
```

# Run Steps (Python)

```
python main.py
```
This should start a GUI.
# WhatsApp Memory Graph MVP

## Architecture
- `main.py` keeps rendering + gestures.
- `backend/src/server.js` receives WhatsApp events, stores memory in JSON, and exposes HTTP API.
- `memory_bridge.py` fetches `/memory/graph` and maps API nodes to HoloMind notes.

## Why JSON and not SQLite/Postgres right now
- Faster MVP iteration and zero setup.
- Works offline and avoids schema migration in early stage.
- You can migrate later without changing HoloMind UI protocol (`/memory/graph`).

## Backend setup
```powershell
cd backend
copy .env.example .env
npm install
npm start
```

## Required env vars
- `OPENAI_API_KEY`: your API key.
- `WHATSAPP_ENABLED=true`: enables Baileys QR login.
- `WHATSAPP_AUTO_REPLY=false`: start safe with manual mode.
- `VOICE_ENABLED=true`: enables TTS send mode on `/memory/send`.
- `VOICE_SERVER_URL=http://127.0.0.1:17493`: local voice server URL.
- `VOICE_PROFILE_ID=gabriel` and `VOICE_LANGUAGE=pt`: default voice profile/language.

## Python app setup
```powershell
pip install -r requirements.txt
python main.py
```

Optional environment variables:
- `HOLOMIND_MEMORY_API` (default `http://127.0.0.1:8787`)
- `HOLOMIND_SYNC_INTERVAL` in seconds (default `10`)

## New keys in HoloMind
- `G`: force sync from Memory API.
- `M`: generate summary for selected chat node and send to WhatsApp.
- `T`: compose and send message to selected chat (audio by default, via local TTS server).

## Useful test endpoints
- `GET /health`
- `GET /memory/graph`
- `GET /memory/chats`
- `POST /memory/messages` (manual seed)
- `POST /memory/summaries/:chatId`
- `POST /memory/send/:chatId`

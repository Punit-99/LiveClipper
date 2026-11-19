# LiveClipper 🎬⚡

Automated live stream monitoring, AI highlight detection, 9:16 vertical video reframing, animated captions, and YouTube Shorts publication pipeline.

---

## 🚀 Quick Start (One Command with Docker Compose)

Start the entire system (FastAPI backend + React frontend + all ML and FFmpeg dependencies) with a single command:

```bash
docker compose up --build
```
Or using the universal CLI runner:
```bash
python backend/run.py docker
```

Once running:
- **Web Dashboard**: [http://localhost:3000](http://localhost:3000)
- **FastAPI Backend & Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **WebSocket Live Feed**: `ws://localhost:3000/ws/live`

To stop all containers:
```bash
docker compose down
# or
python backend/run.py docker-down
```

---

## 💻 Standalone Local Setup (Without Docker)

### 1. Requirements
- Python 3.11+
- FFmpeg (with libass enabled)
- Node.js 18+ and npm

### 2. Backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the API dashboard server
python server.py --port 8000
```

### 3. Frontend Development
```bash
cd frontend
npm install --legacy-peer-deps
npm run dev
```

---

## 🛠️ CLI Runner Commands

You can also use the terminal CLI runner for headless operations:

```bash
# Monitor live streams
python backend/run.py monitor

# Process discovered clips into vertical Shorts
python backend/run.py process

# List processed Shorts ready for upload
python backend/run.py list

# Upload Shorts to YouTube
python backend/run.py upload

# Run automated test suite
python backend/run.py test
```

---

## 📁 Repository Structure

```
live-clipper/
├── docker-compose.yml           # Unified multi-service orchestration
├── backend/                     # LiveClipper Python Backend & API
│   ├── Dockerfile               # Production python:3.11-slim container
│   ├── requirements.txt         # Pinned backend & ML dependencies
│   ├── server.py                # Dashboard ASGI server entrypoint
│   ├── run.py                   # Universal CLI runner
│   ├── app/
│   │   ├── api/                 # FastAPI REST and WebSocket routes
│   │   ├── video/               # Vertical 9:16 reframing, ASS captions, hooks
│   │   ├── detection/           # Chat rate spike & emote burst detection
│   │   ├── analysis/            # Gemini highlight analysis worker
│   │   ├── recording/           # Streamlink capture & rolling buffer
│   │   ├── multistream/         # Multi-stream concurrent manager
│   │   └── shorts/              # Pipeline orchestration & YouTube uploader
│   ├── clips/                   # Extracted source clips (persistent volume)
│   ├── shorts/                  # Generated vertical Shorts (persistent volume)
│   └── tests/                   # 300 automated unit tests
└── frontend/                    # Modern React Web Dashboard
    ├── Dockerfile               # Multi-stage node:20-alpine -> nginx:alpine
    ├── nginx.conf               # SPA routing & API/WebSocket reverse proxy
    ├── src/
    │   ├── pages/               # Dashboard, Clips, Shorts, LiveFeed, Settings
    │   ├── components/          # Navbar and shared UI widgets
    │   └── api.js               # REST & WebSocket client
    └── dist/                    # Compiled production assets
```

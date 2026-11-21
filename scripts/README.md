# Running LiveClipper with Docker

Works on **Linux**, **macOS** (Intel + Apple Silicon), and **Windows 10/11** with Docker Desktop or Docker Engine. Base images are multi-arch (`linux/amd64` and `linux/arm64`) so Docker pulls the right variant automatically.

## Prerequisites

| OS      | Install                                                                 |
|---------|-------------------------------------------------------------------------|
| Linux   | Docker Engine 24+ with the Compose plugin                               |
| macOS   | [Docker Desktop](https://www.docker.com/products/docker-desktop/) 4.30+ |
| Windows | Docker Desktop 4.30+ with the WSL 2 backend                             |

Verify: `docker --version && docker compose version`

## First run

### Linux / macOS

```bash
./scripts/bootstrap.sh
```

### Windows (PowerShell)

```powershell
./scripts/bootstrap.ps1
```

The bootstrap script:
1. Creates `backend/clips`, `shorts`, `imports`, `.buffer`, `logs` on the host.
2. Touches `backend/highlights.jsonl` and `backend/.youtube_tokens.json` so Docker doesn't materialize them as directories.
3. Copies `backend/.env.example` → `backend/.env` if no `.env` is present. **Edit `backend/.env` before you run the app for real.**
4. Builds both images and runs `docker compose up -d`.

## Everyday commands

```bash
docker compose up -d          # start
docker compose ps             # status
docker compose logs -f        # tail logs
docker compose down           # stop
docker compose build --pull   # rebuild (pulls fresh base images)
```

## Access

| Service           | URL                               |
|-------------------|-----------------------------------|
| App (SPA)         | http://localhost:3000             |
| Backend API       | http://localhost:8000/api/...     |
| Health check      | http://localhost:8000/api/health  |

## Notes for each platform

- **Apple Silicon (M1/M2/M3)**: Docker auto-pulls `linux/arm64` for all three base images. First build downloads an arm64 CPU-only PyTorch wheel. If you ever want to force a specific architecture, set `DOCKER_DEFAULT_PLATFORM=linux/amd64` in your shell before building.
- **Windows**: Keep source in a WSL 2 filesystem path (e.g. `\\wsl.localhost\...`) if build speed matters — bind mounts between Windows and the Linux VM are the common speed bottleneck.
- **Line endings**: `.gitattributes` enforces LF for shell scripts, Dockerfiles, and config files so Windows checkouts don't break scripts that run inside the Linux containers.

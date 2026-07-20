# Llama Web UI

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker Image](https://img.shields.io/docker/v/vm75/llama-web-ui?label=Docker%20Hub)](https://hub.docker.com/r/vm75/llama-web-ui)

A lightweight, minimal control plane for [`llama.cpp`](https://github.com/ggerganov/llama.cpp). It runs inside a rootless Podman/Docker container and uses a Python Flask backend paired with a vanilla HTML/JS/Tailwind frontend.

It manages compiling `llama.cpp` from source, handling model uploads, and controlling the process lifecycle of `llama-server` — all from a single-page web UI.

## Features

- **No Bloat:** Built with vanilla HTML/JS and Tailwind CSS via CDN. Backend is a single Python Flask file. No Node.js, Webpack, React, databases, or complex setup.
- **Dynamic Configuration:** Configure `llama.cpp` CMake build parameters (CPU, CUDA, ROCm, Vulkan presets) and runtime `llama-server` flags directly from the UI.
- **Process Management:** Start, stop, and monitor the `llama-server` lifecycle. Logs stream in real-time.
- **Rootless Podman Support:** Designed from the ground up to be compatible with rootless Podman containers.

## How It Works

1. **Build** — The UI clones `llama.cpp` from GitHub, runs `cmake` with your configured flags, and builds the `llama-server` binary inside the container.
2. **Configure** — Runtime parameters (`--threads`, `--ctx-size`, `--model`, etc.) are stored in a `config.json` and passed as CLI flags to `llama-server`.
3. **Run** — The Flask backend manages the `llama-server` process via `subprocess`. Saving config auto-restarts the server.

## Quick Start

### 1. Copy the sample compose file

```bash
cp docker-compose.yml.sample docker-compose.yml
```

### 2. (Optional) Configure ports

Copy and edit the environment file to change default ports:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `LLAMA_WEB_UI_PORT` | `5000` | Port for the control plane web UI |
| `LLAMA_SERVER_PORT` | `8080` | Port for the llama-server inference API |

### 3. Start the container

```bash
docker compose up -d
# or with Podman
podman-compose up -d
```

The Web UI will be available at `http://localhost:5000`. The llama-server inference API runs on `http://localhost:8080`.

### Using Docker Run

```bash
docker run -d \
  --name llama-server \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./data:/home/llama/app/data:Z \
  vm75/llama-web-ui
```

> **Podman users:** add `--userns=keep-id` to avoid permission issues with volume mounts.

## Models

Models are stored in `./data/models/` on the host (mounted to `/home/llama/app/data/models` inside the container). You can:
- **Download from Hugging Face** — directly download `.gguf` files via the UI using a repo/filename or URL.
- **Upload via the web UI** — works well for smaller models.
- **Copy directly** — for models >2 GB, place `.gguf` files straight into `./data/models/`.

## Development

### Makefile Targets

The project includes a `Makefile` for common Podman workflows:

```bash
make build    # Build the container image
make run      # Start services via podman compose
make test     # Health-check the running Web UI
make stop     # Stop services
make clean    # Remove containers (keeps volumes)
make logs     # Tail container logs
make sh       # Shell into the running container
```

### Running Locally (without Docker)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r server/requirements.txt
python server/server.py
```

### Building the Image

```bash
docker build -t llama-web-ui .
```

## License

[MIT](LICENSE)

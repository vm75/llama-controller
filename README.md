# Llama Web UI

A lightweight, minimal control plane for `llama.cpp`. It runs inside a rootless Podman/Docker container and uses a Python Flask backend paired with a vanilla HTML/JS/Tailwind frontend.

It manages compiling `llama.cpp` from source, handling model uploads, and controlling the process lifecycle of `llama-server` based on a JSON configuration file.

## Features

- **No Bloat:** Built with vanilla HTML/JS and Tailwind CSS via CDN. Backend is a single Python Flask file (`server.py`). No Node.js, Webpack, React, databases, or complex setup.
- **Dynamic Configuration:** Easily configure `llama.cpp` build parameters (e.g. for GPU acceleration) and runtime `llama-server` flags directly from the UI.
- **Process Management:** Start, stop, and monitor the `llama-server` lifecycle effortlessly.
- **Rootless Podman Support:** Designed from the ground up to be compatible with rootless Podman containers, keeping things secure.

## Running with Docker / Podman

The easiest way to run Llama Web UI is via the pre-built Docker image available on Docker Hub: [`vm75/llama-web-ui`](https://hub.docker.com/r/vm75/llama-web-ui).

### Using Docker Compose (Recommended)

Create a `docker-compose.yml` file:

```yaml
services:
  llama-web-ui:
    image: vm75/llama-web-ui
    container_name: llama-server
    # Crucial flag that maps your host user ID to the llama user inside the container for rootless Podman
    userns_mode: keep-id
    ports:
      - "${LLAMA_WEB_UI_PORT:-5000}:5000"
      - "${LLAMA_SERVER_PORT:-8080}:8080"
    volumes:
      # The :Z flag handles SELinux permissions automatically
      # Single data volume: holds config.json, models/, and any other runtime state
      - ./data:/home/llama/app/data:Z
      # Optional: cache the llama.cpp checkout & build between container restarts
      # - ./data/llama.cpp:/home/llama/app/llama.cpp:Z
      # Optional: cache the Python venv between container restarts
      # - ./data/venv:/home/llama/app/venv:Z
    restart: unless-stopped
```

Then run:
```bash
docker compose up -d
# or using podman
podman-compose up -d
```

The Web UI will be available at `http://localhost:5000` (or whichever port you've mapped via `.env`). The Llama.cpp server will run on `http://localhost:8080`.

### Using Docker Run

```bash
docker run -d \
  --name llama-server \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./data:/home/llama/app/data:Z \
  vm75/llama-web-ui
```

*(Note for Podman users: you might want to add `--userns=keep-id` to avoid permission issues with volume mounts).*

## Models

Models are stored in the `/home/llama/app/data/models` directory inside the container (which maps to `./data/models` on the host via the single `data` volume mount). You can upload models via the web interface, or for larger models (>2GB), it's highly recommended to place them directly in `./data/models/` on your host.

## Development

To build the image locally:

```bash
docker build -t llama-web-ui .
```

To run the Flask server locally (without Docker):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r server/requirements.txt
python server/server.py
```

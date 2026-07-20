# Llama Controller

[![GitHub Repository](https://img.shields.io/badge/GitHub-vm75%2Fllama--controller-181717?style=flat&logo=github)](https://github.com/vm75/llama-controller)
[![Docker Image](https://img.shields.io/docker/v/vm75/llama-controller?label=Docker%20Hub)](https://hub.docker.com/r/vm75/llama-controller)
[![Docker Pulls](https://img.shields.io/docker/pulls/vm75/llama-controller)](https://hub.docker.com/r/vm75/llama-controller)
[![Build Status](https://img.shields.io/github/actions/workflow/status/vm75/llama-controller/docker-publish.yml?branch=main&label=build)](https://github.com/vm75/llama-controller/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Podman Ready](https://img.shields.io/badge/Podman-Rootless%20Ready-892CA0?logo=podman&logoColor=white)](#)

A lightweight, minimal control plane for [`llama.cpp`](https://github.com/ggml-org/llama.cpp). It runs inside a rootless Podman/Docker container and uses a Python Flask backend paired with a vanilla HTML/JS/Tailwind frontend.

It manages compiling `llama.cpp` from source, downloading model files, configuring model-serving presets, and controlling the process lifecycle of `llama-server` — all from a single-page web UI.

## Features

- **No Bloat:** Built with vanilla HTML/JS and Tailwind CSS via CDN. Backend is a single Python Flask file. No Node.js, Webpack, React, databases, or complex setup.
- **Model Presets:** Add, edit, duplicate, and delete `llama-server` model presets from the UI. Refresh presets automatically adds missing GGUF files and cleans up stale presets. Preset and global parameters are stored in `data/models.ini`.
- **Decoupled Model Storage:** Downloading, uploading, or deleting a GGUF file does not restart or change the models served by `llama-server`.
- **Build Presets:** Select CPU, BLAS, CUDA, ROCm, or Vulkan in the UI to configure both CMake flags and required apt packages in one place.
- **Process Management:** Start, stop, and monitor the `llama-server` lifecycle. Logs stream in real-time.
- **Rootless Podman Support:** Designed from the ground up to be compatible with rootless Podman containers.

## How It Works

1. **Build** — The UI clones `llama.cpp` from GitHub, runs `cmake` with your configured flags, and builds the `llama-server` binary inside the container.
2. **Download** — GGUF files are stored independently in `data/models/`; file changes do not restart the server.
3. **Configure** — Serving presets and inherited global parameters are stored in `data/models.ini` using the native `llama.cpp` model-preset format.
4. **Run** — The Flask backend launches `llama-server --models-preset data/models.ini`. Saving preset changes restarts the server.

## Quick Start

### 1. Copy the sample compose file

```bash
cp docker-compose.yml.example docker-compose.yml
```

### 2. (Optional) Configure ports

Copy and edit the environment file to change default ports:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `LLAMA_CONTROLLER_PORT` | `5000` | Port for the control plane web UI |
| `LLAMA_SERVER_PORT` | `8080` | Port for the llama-server inference API |
| `LLAMA_CPP_REPO` | `https://github.com/ggml-org/llama.cpp.git` | Custom `llama.cpp` Git repository URL |
| `LLAMA_CPP_BRANCH` | `master` | Target `llama.cpp` branch to clone/build |

### 3. Start the container

```bash
docker compose up -d
# or with Podman
podman-compose up -d
```

The Web UI will be available at `http://localhost:5000`. The llama-server inference API runs on `http://localhost:8080`.

Choose a build preset in the Web UI before building. A preset fills in both the CMake flags and any apt packages needed by that backend; clicking **Save & Build** saves both values to `data/config.json` before starting the build. The fields remain editable for custom builds. There is no separate Docker package setting.

### Using Docker Run

```bash
docker run -d \
  --name llama-controller \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./data:/home/llama/app/data:Z \
  vm75/llama-controller
```

> **Podman users:** add `--userns=keep-id` to avoid permission issues with volume mounts.

## Models

Models are stored in `./data/models/` on the host (mounted to `/home/llama/app/data/models` inside the container). You can:

- **Download from Hugging Face** — directly download `.gguf` files via the UI using a repo/filename or URL.
- **Upload via the web UI** — works well for smaller models.
- **Copy directly** — for models >2 GB, place `.gguf` files straight into `./data/models/`.

Model files are not automatically served. After an upload or download, the UI opens the preset editor with that file selected; save it to expose the model or cancel to keep it as storage only. In **Model Serving Presets**, you can also add a named preset manually, choose its local model file, duplicate an existing preset (auto-generating a unique running number suffix), or use **Refresh Presets** to auto-add presets for unassigned GGUF files and remove presets whose files are missing. The UI writes a section like this to `./data/models.ini`:

```ini
[*]
threads = 4
ctx-size = 8192

[coding-model]
model = data/models/coding-model.Q4_K_M.gguf
n-gpu-layers = 99
temp = 0.2
```

Use the **Global Parameters** button in the preset section to edit the `[*]` settings inherited by every preset. A preset remains editable if its model file is missing, and the UI marks it with a warning until the path is repaired or the file is restored.

Deleting a model file also removes every serving preset that references it, then restarts `llama-server` so the router immediately reflects the new preset list.

On upgrade, existing runtime `params` from `data/config.json` are migrated to the `[*]` section when `data/models.ini` is first created. Boolean flags are written as `true`.

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
docker build -t llama-controller .
```

## License

[MIT](LICENSE)

# llama-web-ui

[![Docker Image](https://img.shields.io/docker/v/vm75/llama-web-ui?label=Docker%20Hub)](https://hub.docker.com/r/vm75/llama-web-ui)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

A lightweight, containerized control plane for [`llama.cpp`](https://github.com/ggml-org/llama.cpp). `llama-web-ui` provides a clean single-page interface to compile `llama.cpp` from source, download and manage GGUF models, configure native `models.ini` serving presets, and control the `llama-server` inference process lifecycle.

---

## Features

- 🚀 **Zero-Bloat UI:** Single-page frontend using vanilla JS and Tailwind CSS via CDN paired with a lightweight Flask backend. No Node.js, Webpack, or database dependencies.
- 🛠️ **In-Container Source Build:** Compile `llama.cpp` from source directly from the web UI with support for CPU, OpenBLAS, CUDA, ROCm, and Vulkan build presets and automatic `apt-get` package management.
- 📦 **Hugging Face Downloader:** Search and download GGUF models directly from Hugging Face with real-time SSE progress streaming.
- ⚙️ **Native Model Presets:** Add, edit, duplicate, and refresh serving presets stored in `data/models.ini`. Define inherited global parameters (`[*]`) and per-model flags (`n-gpu-layers`, `ctx-size`, `temp`, etc.).
- 🔄 **Decoupled Lifecycle:** File downloads/uploads and preset configurations do not restart `llama-server` prematurely—restarts happen only when presets are modified or deleted.
- 🔒 **Rootless Podman Ready:** Purpose-built to run as a non-root user (`llama`) with full rootless Podman and Docker support.

---

## Quick Start

### Option 1: Docker Run

```bash
docker run -d \
  --name llama-web-ui \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./data:/home/llama/app/data:Z \
  vm75/llama-web-ui:latest
```

> **Note for Podman users:** Add `--userns=keep-id` to preserve local file ownership:
> ```bash
> podman run -d \
>   --name llama-web-ui \
>   --userns=keep-id \
>   -p 5000:5000 \
>   -p 8080:8080 \
>   -v ./data:/home/llama/app/data:Z \
>   vm75/llama-web-ui:latest
> ```

### Option 2: Docker Compose / Podman Compose

Create a `docker-compose.yml` file:

```yaml
version: '3.8'

services:
  llama-server:
    image: vm75/llama-web-ui:latest
    container_name: llama-server
    ports:
      - "${LLAMA_WEB_UI_PORT:-5000}:5000"
      - "${LLAMA_SERVER_PORT:-8080}:8080"
    environment:
      - LLAMA_WEB_UI_PORT=${LLAMA_WEB_UI_PORT:-5000}
      - LLAMA_SERVER_PORT=${LLAMA_SERVER_PORT:-8080}
      - LLAMA_CPP_REPO=${LLAMA_CPP_REPO:-https://github.com/ggml-org/llama.cpp.git}
      - LLAMA_CPP_BRANCH=${LLAMA_CPP_BRANCH:-master}
    volumes:
      - ./data:/home/llama/app/data:Z
    restart: unless-stopped
    userns_mode: keep-id
```

Run:
```bash
docker compose up -d
# or with Podman
podman-compose up -d
```

Once running, access:
- **Web UI:** `http://localhost:5000`
- **llama-server API:** `http://localhost:8080`

---

## Ports & Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLAMA_WEB_UI_PORT` | `5000` | Port for the control plane web UI |
| `LLAMA_SERVER_PORT` | `8080` | Port for the `llama-server` OpenAI-compatible API |
| `LLAMA_CPP_REPO` | `https://github.com/ggml-org/llama.cpp.git` | `llama.cpp` Git repository URL |
| `LLAMA_CPP_BRANCH` | `master` | Target `llama.cpp` branch to clone/build |

---

## Volumes & Directory Structure

Mount host directory `./data` to `/home/llama/app/data`:

```text
data/
├── config.json       # Build presets and UI options
├── models.ini        # Native llama-server model serving presets
└── models/           # Directory for GGUF model files
```

- **GGUF Models:** Downloaded or uploaded models are stored under `data/models/`.
- **Large Models (>2 GB):** Copy `.gguf` files directly into `./data/models/` on the host, then click **Refresh Presets** in the UI to create serving presets automatically.

---

## Hardware Acceleration & Build Presets

The image includes build tools (`cmake`, `g++`, `git`, `python3-venv`). You can select or customize build presets directly in the UI before building:

- **CPU:** Default native CPU build.
- **CPU-BLAS:** Optimized CPU build using `libopenblas-dev`.
- **NVIDIA CUDA:** GPU acceleration requiring `nvidia-cuda-toolkit` and NVIDIA container runtime.
- **AMD ROCm:** GPU acceleration using `hipcc`.
- **Vulkan:** Cross-platform GPU acceleration using `libvulkan-dev`.

---

## License

Distributed under the [MIT License](https://opensource.org/licenses/MIT).

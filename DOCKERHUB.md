# llama-controller

[![GitHub Repository](https://img.shields.io/badge/GitHub-vm75%2Fllama--controller-181717?style=flat&logo=github)](https://github.com/vm75/llama-controller)
[![Docker Image](https://img.shields.io/docker/v/vm75/llama-controller?label=Docker%20Hub)](https://hub.docker.com/r/vm75/llama-controller)
[![Docker Pulls](https://img.shields.io/docker/pulls/vm75/llama-controller)](https://hub.docker.com/r/vm75/llama-controller)
[![Build Status](https://img.shields.io/github/actions/workflow/status/vm75/llama-controller/docker-publish.yml?branch=main&label=build)](https://github.com/vm75/llama-controller/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Podman Ready](https://img.shields.io/badge/Podman-Rootless%20Ready-892CA0?logo=podman&logoColor=white)](#)

> 📁 **Source Code & Issue Tracker:** [github.com/vm75/llama-controller](https://github.com/vm75/llama-controller)

A lightweight, containerized control plane for [`llama.cpp`](https://github.com/ggml-org/llama.cpp). `llama-controller` provides a clean single-page interface to compile `llama.cpp` from source, download and manage GGUF models, configure native `models.ini` serving presets, and control the `llama-server` inference process lifecycle.

---

## Features

- 🚀 **Zero-Bloat UI:** Single-page frontend using vanilla JS and Tailwind CSS via CDN paired with a lightweight Flask backend. No Node.js, Webpack, or database dependencies.
- 🛠️ **In-Container Source Build:** Compile `llama.cpp` from source directly from the web UI with support for CPU, OpenBLAS, CUDA, ROCm, and Vulkan build presets and automatic `apt-get` package management.
- 📦 **Hugging Face Downloader:** Search and download GGUF models directly from Hugging Face with real-time SSE progress streaming.
- ⚙️ **Native Model Presets:** Add, edit, duplicate, and refresh serving presets stored in `data/models.ini`. Define inherited global parameters (`[*]`) and per-model flags (`n-gpu-layers`, `ctx-size`, `temp`, etc.).
- 🔄 **Decoupled Storage Lifecycle:** File downloads/uploads open a prefilled preset modal without changing the active server; server restarts occur only when model presets are saved, modified, or deleted.
- 🔒 **Rootless Podman Ready:** Purpose-built to run as a non-root user (`llama`) with full rootless Podman and Docker support.

---

## Quick Start

### Option 1: Docker Run

```bash
docker run -d \
  --name llama-controller \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./data:/home/llama/app/data:Z \
  vm75/llama-controller:latest
```

> **Note for Podman users:** Add `--userns=keep-id` to preserve local file ownership:
> ```bash
> podman run -d \
>   --name llama-controller \
>   --userns=keep-id \
>   -p 5000:5000 \
>   -p 8080:8080 \
>   -v ./data:/home/llama/app/data:Z \
>   vm75/llama-controller:latest
> ```

### Option 2: Docker Compose / Podman Compose

Create a `docker-compose.yml` file:

```yaml
services:
  llama-controller:
    image: vm75/llama-controller:latest
    container_name: llama-controller
    ports:
      - "${LLAMA_CONTROLLER_PORT:-5000}:5000"
      - "${LLAMA_SERVER_PORT:-8080}:8080"
    environment:
      - LLAMA_CONTROLLER_PORT=${LLAMA_CONTROLLER_PORT:-5000}
      - LLAMA_SERVER_PORT=${LLAMA_SERVER_PORT:-8080}
      - LLAMA_SERVER_URL=${LLAMA_SERVER_URL:-http://localhost:8080}
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
| `LLAMA_CONTROLLER_PORT` | `5000` | Port for the control plane web UI |
| `LLAMA_SERVER_PORT` | `8080` | Port for the `llama-server` OpenAI-compatible API |
| `LLAMA_SERVER_URL` | `http://localhost:8080` | Public URL link to `llama-server` displayed in the UI |
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

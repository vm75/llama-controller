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

## Why Llama Controller

`llama.cpp` is a low-overhead local inference engine with extensive serving controls, but tuning it around constrained hardware can otherwise require repeated command-line edits, manual configuration changes, custom rebuilds, and process restarts. Llama Controller keeps those native controls accessible in one lightweight interface without inserting another inference runtime between you and `llama-server`.

Create per-model presets for the `llama-server` options that matter to your machine—such as context size, GPU layer offload, cache quantization, parallelism, speculative decoding, and other supported flags. This makes it straightforward to test different ways of sharing work across GPU memory, system memory, and CPU, then preserve a working configuration per model. Build the engine with the CMake options appropriate for your backend, manage local model files, expose selected models through the router preset file, and restart only when serving settings actually change.


## Example tuning workflow

Llama Controller exposes native `llama-server` keys in every model preset. For a large MoE model, begin by keeping a limited number of expert layers in system RAM while offloading the rest:

```ini
n-gpu-layers = all
n-cpu-moe = 12
ctx-size = 32768
```

For a context-bound workload, quantize the K and V cache instead of leaving it at F16:

```ini
ctx-size = 65536
cache-type-k = q8_0
cache-type-v = q8_0
```

The first example trades some throughput for lower VRAM use. The second frees memory used by the KV cache, which can make more context or model layers fit in VRAM, but it does not add physical VRAM and can carry quality, compatibility, or performance tradeoffs.

For 1.7-bit ternary models such as [`prism-ml/Ternary-Bonsai-27B-gguf`](https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf), create a **Build Profile** for `https://github.com/PrismML-Eng/llama.cpp.git`. Build and activate that profile for the custom `Q2_0_g128` kernel support; the upstream main profile remains intact and can be activated again later. Then configure a model preset:

```ini
[ternary-bonsai-27b]
model = data/models/Ternary-Bonsai-27B-Q2_0.gguf
n-gpu-layers = 99
flash-attn = 1
temp = 0.7
top-p = 0.95
top-k = 20
jinja = true
```

Treat these as starting points: adjust one variable at a time and use the built-in **Llama Help** view to confirm options for your current build.

---


## Features

- 🚀 **Zero-Bloat UI:** Single-page frontend using vanilla JS and Tailwind CSS via CDN paired with a lightweight Flask backend. No Node.js, Webpack, or database dependencies.
- 🛠️ **Independent Build Profiles:** Keep multiple repository, branch, CMake, and package configurations with separate persistent source/build trees. Activate one profile to run while preserving the others. CPU, OpenBLAS, CUDA, ROCm, and Vulkan templates fill common settings.
- 📦 **Hugging Face Downloader:** Search and download GGUF models directly from Hugging Face with real-time SSE progress streaming.
- ⚙️ **Compatible Model Presets:** Add, edit, duplicate, and refresh serving presets stored in `data/models.ini`. Assign each preset to one or more Build Profiles so the active `llama-server` receives only models its build supports. Define inherited global parameters (`[*]`) and per-model flags (`n-gpu-layers`, `ctx-size`, `temp`, etc.).
- 👁️ **Multimodal and MTP Companions:** Upload or download `mmproj-*.gguf` projectors and `mtp-*.gguf` draft files, then attach them through dedicated preset fields. MTP selection configures `spec-draft-model` and `draft-mtp` automatically.
- 🔄 **Decoupled Storage Lifecycle:** Primary downloads/uploads open a prefilled preset modal without changing the active server; companions remain stored until attached. Server restarts occur only when model presets or their file references change.
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
    volumes:
      - ./data:/home/llama/app/data:Z
      # Optional: persist profile source/build trees like the Python venv
      # - ./build-profiles:/home/llama/app/build-profiles:Z
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

---

## Volumes & Directory Structure

Mount host directory `./data` to `/home/llama/app/data`:

```text
data/
├── config.json       # Build profiles, templates, and UI options
├── models.ini        # Native llama-server model serving presets
└── models/           # Directory for GGUF model files

build-profiles/      # Optional mount: separate source/build tree per profile
```

- **Build Profiles:** Repository URL, branch, CMake flags, and packages are saved in `data/config.json`; checkouts and binaries live under `build-profiles/<profile-id>/llama.cpp/`. Mount `./build-profiles:/home/llama/app/build-profiles:Z` to persist them across container recreation, or leave it unmounted for ephemeral build trees. Each serving preset can be assigned to compatible profiles; the active server gets a filtered router file containing only matching presets. Deleting a profile leaves its checkout on disk.
- **GGUF Files:** Downloaded or uploaded primary models, multimodal projectors, and MTP draft files are stored under `data/models/`.
- **Companions:** Filenames beginning with `mmproj` or `mtp` are labeled as companions and excluded from automatic standalone presets. Attach them while adding or editing the primary model's serving preset.
- **Large Files (>2 GB):** Copy `.gguf` files directly into `./data/models/` on the host, then click **Refresh Presets** in the UI to create presets for unassigned primary models. Companion files remain available for manual attachment.
- **Deletion:** Removing a primary model deletes its serving presets; removing a referenced companion only detaches it. The server restarts only when preset configuration changes.

---

## Build Profiles & Hardware Templates

The image includes build tools (`cmake`, `g++`, `git`, `python3-venv`). A first-run `Main llama.cpp` profile targets the upstream repository on `master`. Create or duplicate profiles for other forks, branches, or hardware settings, then use **Save & Set Active** to select which built binary launches. Repository and branch are managed in the UI, not through environment variables. The available hardware templates are:

- **CPU:** Default native CPU build.
- **CPU-BLAS:** Optimized CPU build using `libopenblas-dev`.
- **NVIDIA CUDA:** GPU acceleration requiring `nvidia-cuda-toolkit` and NVIDIA container runtime.
- **AMD ROCm:** GPU acceleration using `hipcc`.
- **Vulkan:** Cross-platform GPU acceleration using `libvulkan-dev`.

---

## License

Distributed under the [MIT License](https://opensource.org/licenses/MIT).

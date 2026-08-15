# Llama Controller

[![GitHub Repository](https://img.shields.io/badge/GitHub-vm75%2Fllama--controller-181717?style=flat&logo=github)](https://github.com/vm75/llama-controller)
[![Docker Image](https://img.shields.io/docker/v/vm75/llama-controller?label=Docker%20Hub)](https://hub.docker.com/r/vm75/llama-controller)
[![Docker Pulls](https://img.shields.io/docker/pulls/vm75/llama-controller)](https://hub.docker.com/r/vm75/llama-controller)
[![Build Status](https://img.shields.io/github/actions/workflow/status/vm75/llama-controller/docker-publish.yml?branch=main&label=build)](https://github.com/vm75/llama-controller/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Podman Ready](https://img.shields.io/badge/Podman-Rootless%20Ready-892CA0?logo=podman&logoColor=white)](#)

A lightweight, minimal control plane for [`llama.cpp`](https://github.com/ggml-org/llama.cpp). It runs inside a rootless Podman/Docker container and uses a Python Flask backend paired with a vanilla HTML/JS/Tailwind frontend.

It manages compiling `llama.cpp` from source, downloading model files, configuring model-serving presets, and controlling the process lifecycle of `llama-server` — all from a single-page web UI.

## Why Llama Controller

Local inference is often constrained by the hardware already on hand: limited GPU memory, modest system RAM, or a CPU that benefits from a backend-specific build. `llama.cpp` provides the low-overhead engine and a broad set of serving controls, but using those controls directly can mean repeatedly editing command lines or configuration files, rebuilding for a different backend, and manually restarting a server whenever models or settings change.

Llama Controller makes that workflow practical without placing another inference layer between you and `llama-server`. It keeps the native `llama.cpp` configuration format visible and editable, so you can create per-model presets for choices such as context size, GPU layer offload, cache quantization, parallelism, speculative decoding, and other supported server flags. That makes it easier to experiment with how a model divides work between GPU memory, system memory, and CPU, then retain the configuration that works best for a particular machine.

It also brings the operational pieces together: rebuild `llama.cpp` with the CMake options your hardware needs, keep several models and companion GGUF files in local storage, expose selected models through the router preset file, and restart only when a serving configuration actually changes. The result is a lightweight, repeatable local-serving setup for people who want the control of native `llama.cpp` with the convenience of a model manager.

## Practical tuning examples

Enter these keys in a model’s per-model parameter editor without leading dashes. They are starting points rather than universal recommendations: change one setting at a time, watch the server log and memory use, and keep the combination that is stable and fast on your hardware. Use the built-in **Llama Help** view for the options supported by the specific `llama.cpp` build you are running.

### MoE model: keep some expert layers in system RAM

A Mixture-of-Experts model can be difficult to fit because it contains many expert weights. `n-cpu-moe` keeps the MoE weights for the first N layers in CPU RAM. That can reserve GPU memory for the remaining model layers, the KV cache, and runtime buffers. For example:

```ini
[large-moe-balanced]
model = data/models/large-moe.gguf
n-gpu-layers = all
n-cpu-moe = 12
ctx-size = 32768
```

Start with a small `n-cpu-moe` value and increase it until the model fits. More CPU-resident experts reduce VRAM pressure, but may lower throughput because expert weights must be used from system RAM. Adequate system RAM and memory bandwidth matter.

### Longer context: quantize the KV cache

The KV cache grows with context length. The following uses the native `llama.cpp` Q8 cache types instead of the default F16 cache types:

```ini
[long-context-q8-cache]
model = data/models/model.gguf
n-gpu-layers = all
ctx-size = 65536
cache-type-k = q8_0
cache-type-v = q8_0
```

KV-cache quantization frees memory where the cache is placed, often GPU VRAM when KV offload is enabled. That freed capacity can help a longer context fit or leave room for more model layers in VRAM; it does not increase the physical size of the GPU. Lower-precision cache types can have quality, compatibility, or performance tradeoffs, so validate them with the model and workload you intend to serve.

### Ternary models: custom fork & 1.7-bit weights (e.g. Ternary Bonsai 27B)

Ternary models such as [`prism-ml/Ternary-Bonsai-27B-gguf`](https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf) use 1.71-bit weight representations (`Q2_0_g128`) requiring specialized low-bit kernels. To run this model in Llama Controller:

1. In **llama-server Build Profiles**, create a profile named `PrismML Ternary`, set its repository to `https://github.com/PrismML-Eng/llama.cpp.git`, and choose the required branch and hardware template.
2. Click **Save & Build** for that profile, then **Save & Set Active** when you want its binary to run. The main `llama.cpp` profile and build remain available.
3. Download `Ternary-Bonsai-27B-Q2_0.gguf` from Hugging Face via the web UI (or copy it into `data/models/`).
4. Set up a serving preset in `models.ini` and choose only the `PrismML Ternary` Build Profile in the preset’s **Compatible Build Profiles** field:
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


## Features

- **No Bloat:** Built with vanilla HTML/JS and Tailwind CSS via CDN. Backend is a single Python Flask file. No Node.js, Webpack, React, databases, or complex setup.
- **Model Presets:** Add, edit, duplicate, and delete `llama-server` model presets from the UI. Assign each preset to one or more compatible Build Profiles; the active server receives only its supported presets. Refresh presets automatically adds missing GGUF files and cleans up stale presets. Preset and global parameters are stored in `data/models.ini`.
- **Companion GGUF Files:** Store and attach `mmproj-*.gguf` multimodal projectors and `mtp-*.gguf` speculative draft files through dedicated preset fields, with missing-file warnings in the UI.
- **Decoupled Model Storage:** Downloads and uploads do not restart or change the models served by `llama-server`. Deletion restarts only when a preset or companion reference changes.
- **Build Profiles:** Keep multiple independent `llama-server` source/build profiles, each with its own Git repository, branch, CMake flags, and apt packages. Select one active profile to run while preserving every other checkout and build.
- **Build Templates:** Select CPU, BLAS, CUDA, ROCm, or Vulkan to fill both CMake flags and required apt packages for the current profile.
- **Process Management:** Start, stop, and monitor the `llama-server` lifecycle. Logs stream in real-time.
- **Rootless Podman Support:** Designed from the ground up to be compatible with rootless Podman containers.

## How It Works

1. **Build** — The UI creates independent source/build trees under `build-profiles/`. A first-run `Main llama.cpp` profile targets the upstream repository, and additional profiles can target other repositories, branches, or hardware settings without replacing it.
2. **Download** — Primary models and optional mmproj/MTP companion GGUF files are stored independently in `data/models/`; downloads do not restart the server.
3. **Configure** — Serving presets, companion paths, inherited global parameters, and optional compatible Build Profile assignments are stored in `data/models.ini`.
4. **Run** — Before launch, the Flask backend generates a filtered temporary preset file for the active build profile. The active `llama-server` receives only compatible models; unassigned presets remain compatible with every profile.

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
| `LLAMA_SERVER_URL` | `http://localhost:8080` | Public URL link to llama-server displayed in the UI |

### 3. Start the container

```bash
docker compose up -d
# or with Podman
podman-compose up -d
```

The Web UI will be available at `http://localhost:5000`. The llama-server inference API runs on `http://localhost:8080`.

The first run includes a `Main llama.cpp` build profile for `https://github.com/ggml-org/llama.cpp.git` on `master`. Choose a hardware template and click **Save & Build**. Create or duplicate profiles for forks, branches, or different build settings; each profile stores its repository, branch, CMake flags, and apt packages in `data/config.json` and keeps its own checkout under `build-profiles/`. **Save & Set Active** selects the binary used at startup and for later restarts. Repository and branch are no longer configured through Docker environment variables.

For persistence across container recreation, optionally mount `./build-profiles` to `/home/llama/app/build-profiles`, just like the optional `venv` mount in the sample Compose file.

### Using Docker Run

```bash
docker run -d \
  --name llama-controller \
  -p 5000:5000 \
  -p 8080:8080 \
  -v ./data:/home/llama/app/data:Z \
  -v ./build-profiles:/home/llama/app/build-profiles:Z \
  vm75/llama-controller
```

> **Podman users:** add `--userns=keep-id` to avoid permission issues with volume mounts.

## Models

Models are stored in `./data/models/` on the host (mounted to `/home/llama/app/data/models` inside the container). You can:

- **Download from Hugging Face** — directly download `.gguf` files via the UI using a repo/filename or URL.
- **Upload via the web UI** — works well for smaller models.
- **Copy directly** — for models >2 GB, place `.gguf` files straight into `./data/models/`.

Primary model files are not automatically served. After a primary upload or download, the UI opens the preset editor with that file selected; save it to expose the model or cancel to keep it as storage only. Files whose names start with `mmproj` or `mtp` are stored as companions instead of opening a new preset. Edit a serving preset to attach a multimodal projector or MTP draft file. Selecting an MTP file writes `spec-draft-model` and adds `draft-mtp` to `spec-type` automatically.

In **Model Serving Presets**, you can also choose **Compatible Build Profiles**. A preset with no restriction is served by every build; a ternary-only preset can be restricted to the matching fork. The controller stores restrictions as comments in `data/models.ini`, then filters them out of the active server’s generated router file. You can also add a named preset manually, choose its local files, duplicate an existing preset (auto-generating a unique running number suffix), or use **Refresh Presets** to auto-add presets for unassigned primary GGUF files and remove presets whose primary model files are missing. Companion files are never turned into standalone presets. The UI writes a section like this to `./data/models.ini`:

```ini
[*]
threads = 4
ctx-size = 8192

# llama-controller-build-profiles = main,prism-ternary
[coding-model]
model = data/models/coding-model.Q4_K_M.gguf
n-gpu-layers = 99
mmproj = data/models/mmproj-coding-model-f16.gguf
spec-draft-model = data/models/mtp-coding-model-q8.gguf
spec-type = draft-mtp
temp = 0.2
```

Use the **Global Parameters** button in the preset section to edit the `[*]` settings inherited by every preset. A preset remains editable if its primary model or a companion file is missing, and the UI marks the missing path until it is repaired or restored.

Deleting a primary model file removes every serving preset that uses it. Deleting an mmproj or MTP companion keeps the preset and removes only the matching companion reference. Either change restarts `llama-server`; deleting an unreferenced file does not.

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

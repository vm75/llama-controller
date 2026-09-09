# Llama Controller — Agent Reference

A lightweight, containerized control plane for `llama.cpp`. Flask backend + vanilla HTML/JS/Tailwind frontend. Manages building `llama.cpp` from source, model files, native `models.ini` serving presets, and the `llama-server` process lifecycle.

## Project Purpose

Llama Controller provides a low-overhead way to operate native `llama.cpp` on hardware-constrained local systems. Its value is making the engine's build and serving controls easy to iterate on: users can preserve per-model `llama-server` flags (for example, context size, GPU offload, cache quantization, parallelism, and speculative decoding), rebuild for a chosen hardware backend, and manage model availability without manually editing files or supervising processes. The application intentionally remains a control plane rather than an inference layer, so `llama-server` retains responsibility for model routing and execution. Public documentation should explain this with cautious, native `models.ini` examples: `n-cpu-moe` can place MoE expert weights for the first N layers in CPU RAM, while `cache-type-k` and `cache-type-v` quantize KV-cache storage to free memory for context or model layers. These are workload-dependent tradeoffs, not guarantees of faster inference or additional physical VRAM. Custom forks (such as `https://github.com/PrismML-Eng/llama.cpp.git` for 1.7-bit ternary models like `prism-ml/Ternary-Bonsai-27B-gguf`) are managed as independent Build Profiles in the UI, keeping the upstream checkout and build intact.

## Core Principles: YAGNI & KISS

### KISS (Keep It Simple, Stupid)
* **No Build Steps for Frontend:** The UI is a single `server/templates/index.html` using Tailwind CSS via CDN. Do not introduce Node.js, npm, Webpack, Vite, React, Vue, or any frontend frameworks.
* **Minimal Backend:** The backend is a single `server/server.py` using Flask. Do not split into multiple modules.
* **Process Management:** We use Python `subprocess` to manage `llama-server`. Do not introduce supervisor, systemd, or Celery.

### YAGNI (You Aren't Gonna Need It)
* **No Databases:** State lives entirely in `config.json` and `models.ini`. No SQLite, PostgreSQL, Redis, etc.
* **No User Authentication:** This is a local/private tool. No login screens, JWT, or sessions.
* **No Bloatware:** Only add dependencies to `requirements.txt` if absolutely necessary.

---

## Project Layout

```
llama-controller/
├── server/                        # All backend + frontend code
│   ├── server.py                  # Flask app — the entire backend
│   ├── requirements.txt           # Python deps: Flask, Werkzeug
│   ├── static/
│   │   ├── docker.svg             # Docker logo icon
│   │   ├── favicon.svg            # App favicon
│   │   └── github.svg             # GitHub logo icon
│   └── templates/
│       └── index.html             # Single-page UI (Jinja2 + Tailwind CDN + vanilla JS)
├── config.default.json            # Default config — seeded into data/config.json on first run
├── models.ini.example             # Default global model parameters — seeded into data/models.ini
├── Dockerfile                     # Debian bookworm-slim, non-root `llama` user, venv
├── docker-compose.yml.example     # Sample compose file (users copy to docker-compose.yml)
├── .env.example                   # Documented port variables
├── Makefile                       # Podman workflow shortcuts (build, run, test, stop, etc.)
├── VERSION                        # Semver string (e.g. "0.1.0"), read by server.py at startup
├── LICENSE                        # MIT
└── AGENTS.md                      # This file
```

### Runtime directories (not in repo, gitignored)
```
data/                              # Persistent volume mount point
├── config.json                    # Live config (seeded from config.default.json)
├── models.ini                     # Global + per-model llama-server serving presets
└── models/                        # Primary, mmproj, and MTP GGUF files
build-profiles/                    # Optional profile source/build mount
└── <profile-id>/llama.cpp/        # One checkout and binary per Build Profile
venv/                              # Python virtual environment (created by Dockerfile)
docker-compose.yml                 # User's local compose (copied from .example)
.env                               # User's local port overrides
```

---

## Architecture

```
┌─────────────────── Container (/home/llama/app) ───────────────────┐
│                                                                    │
│  Flask (server.py) :5000                                           │
│    ├── GET  /              → serves index.html (Jinja2 template)   │
│    ├── API routes          → JSON endpoints for UI                 │
│    └── subprocess.Popen    → manages llama-server child process    │
│                                                                    │
│  llama-server :8080        (binary from the active Build Profile)  │
│    └── started/stopped by Flask via subprocess                     │
│                                                                    │
│  data/ (volume mount)                                              │
│    ├── config.json         (profiles + runtime config, persisted)  │
│    ├── models.ini          (native model serving presets)          │
│    └── models/             (primary + companion GGUF files)        │
│  build-profiles/ (optional mount; isolated repositories/builds)    │
└────────────────────────────────────────────────────────────────────┘
```

**Startup sequence:** `server.py` → `ensure_data_dir()` (seeds/migrates config and model preset files and creates profile storage) → `start_llama()` (launches the active profile if its binary exists) → `app.run(:5000)`.

**Serving flow:** `data/models.ini` is the editable source for every preset. Before launch, `start_llama()` writes `/tmp/llama-active-models.ini`, retaining global parameters and only the presets compatible with the active Build Profile, then launches `llama-server --models-preset /tmp/llama-active-models.ini --host 0.0.0.0 --port 8080`. Uploads and downloads do not restart the process. Primary files open a prefilled preset modal; conventional `mmproj-*` and `mtp-*` files remain stored for attachment through dedicated fields. Presets write companions as `mmproj` and `spec-draft-model`; selecting MTP also ensures `spec-type` includes `draft-mtp`. Deleting a primary removes its presets, while deleting a companion removes only matching references; either restarts only when `models.ini` changed. Saving or refreshing presets also restarts when changed. Individual presets can be duplicated with an auto-generated unique running number suffix.

**Build flow:** Build Profiles in `config.json` each contain a stable ID, display name, Git repository, branch, CMake flags, and required apt packages. `POST /api/build` accepts the selected `profile_id`, installs that profile’s packages via `sudo apt-get`, clones into `build-profiles/<profile-id>/llama.cpp`, fetches/checks out the configured branch, runs `cmake -B build -DCMAKE_BUILD_TYPE=Release {cmake_params}`, and builds the `llama-server` target. Existing checkouts are never deleted or repointed: an origin mismatch fails with instructions to create a new profile. A successful active-profile build restarts the server; an inactive-profile build leaves it running. The default first-run `main` profile targets upstream `ggml-org/llama.cpp` on `master`. Legacy top-level `cmake_params`/`extra_packages` migrate into that profile.

---

## config.json Schema

```jsonc
{
  "active_build_profile": "main",               // Only this profile's binary is launched
  "build_profiles": [
    {
      "id": "main",                             // Stable directory-safe ID
      "name": "Main llama.cpp",                 // UI label
      "repo_url": "https://github.com/ggml-org/llama.cpp.git",
      "branch": "master",
      "cmake_params": "-DGGML_NATIVE=ON ...",   // Appended after -DCMAKE_BUILD_TYPE=Release
      "extra_packages": ""                      // Installed before this profile builds
    }
  ],
  "cmake_presets": [                              // Templates copied into the selected profile
    { "label": "CPU",          "flags": "-DGGML_NATIVE=ON ...", "extra_packages": "" },
    { "label": "CPU-BLAS",     "flags": "-DGGML_BLAS=ON ...",   "extra_packages": "libopenblas-dev" },
    { "label": "NVIDIA CUDA",  "flags": "-DGGML_CUDA=ON ...",   "extra_packages": "nvidia-cuda-toolkit" },
    { "label": "AMD ROCm",     "flags": "-DGGML_HIPBLAS=ON ...", "extra_packages": "hipcc" },
    { "label": "Vulkan",       "flags": "-DGGML_VULKAN=ON ...", "extra_packages": "libvulkan-dev" }
  ],
  "llama_server_url": "http://localhost:8080"
}
```

`config.json` contains build/UI configuration only. Legacy top-level `cmake_params` and `extra_packages` are migrated into the default `main` Build Profile; legacy `params` arrays are migrated to `models.ini` when that file is first created.

## models.ini Schema

```ini
[*]                              # Global parameters inherited by every preset
threads = 4
ctx-size = 8192

[coding-model]                   # Preset name exposed by the router
# llama-controller-build-profiles = main,prism-ternary
model = data/models/model.gguf   # Path relative to the app root, or an absolute path
n-gpu-layers = 99
mmproj = data/models/mmproj-model-f16.gguf
spec-draft-model = data/models/mtp-model-q8.gguf
spec-type = draft-mtp
temp = 0.2
```

Parameter keys match `llama-server` flags without leading dashes. The optional `# llama-controller-build-profiles = <id>,...` comment immediately above a preset assigns one or more compatible Build Profile IDs; omitting it makes the preset compatible with every profile. `GET /api/model-presets` returns those assignments plus active-profile availability and `model_exists`, `mmproj_exists`, and `mtp_exists` values. Conventional companion detection is filename-based: basenames beginning with `mmproj` or `mtp` are excluded from automatic primary presets. Writes normalize the file and preserve only controller compatibility comments. The generated active router file is ephemeral and has no controller comments.

**Hardcoded flags** (not editable in config): `--models-preset /tmp/llama-active-models.ini`, `--host 0.0.0.0`, `--port 8080`.

---

## API Routes

All routes are defined in `server/server.py`. All API responses are JSON.

| Method | Path | Purpose | Side Effects |
|---|---|---|---|
| `GET` | `/` | Serve the single-page UI | — |
| `GET` | `/api/status` | Active profile metadata plus `{ binary_built, server_running }` | — |
| `GET` | `/api/config` | Return full config.json | — |
| `POST` | `/api/config` | Validate and save Build Profiles/UI config | Writes config.json; changing active profile restarts with its binary when available |
| `POST` | `/api/server-url` | Update the `llama_server_url` field | Writes config.json (no restart) |
| `GET` | `/api/models` | List GGUF files with sizes and model/mmproj/MTP kinds | — |
| `POST` | `/api/upload` | Upload a GGUF file | Saves to models/; primary files open a preset modal, companions do not (no restart) |
| `POST` | `/api/download` | Download a GGUF file from HF | Saves to models/ via SSE; primary files open a preset modal, companions do not (no restart) |
| `DELETE` | `/api/models/<filename>` | Delete a model or companion file | Removes primary presets or companion references; restarts only if presets changed |
| `GET` | `/api/model-presets` | Return global parameters, serving presets, compatibility assignments, and active-profile availability | Reads models.ini |
| `PUT` | `/api/model-presets` | Replace global parameters, presets, and compatibility assignments | Writes models.ini, regenerates active router file, restarts process if built |
| `POST` | `/api/model-presets/refresh` | Auto-add primary GGUF presets & remove stale presets | Excludes mmproj/MTP companions; writes models.ini and restarts if changed |
| `POST` | `/api/build` | Build requested `profile_id` in its isolated checkout | Long-running, writes build logs; restarts only when building the active profile |
| `GET` | `/api/logs` | Last 100 lines of server logs | — |
| `GET` | `/api/logs/download` | Download full server log file (`llama-server.log`) | — |
| `GET` | `/api/build-logs` | Last 100 lines of build logs | — |
| `GET` | `/api/build-logs/download` | Download full build log file (`llama-build.log`) | — |
| `GET` | `/api/llama-help` | `llama-server --help` output | — |

---

## Frontend Patterns

- **Single file:** `server/templates/index.html` — Jinja2 template with inline `<script>`.
- **Styling:** Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com">`). No build step.
- **Polling:** Status is polled every 3s; logs every 2s; model files and presets every 10s via `setInterval` + `fetch`. The tabbed log panel provides one-click copy to clipboard and log file downloading for both server and build logs.
- **Preset UI:** Serving presets are listed in their own panel. Add/edit uses a modal with primary model, optional mmproj, optional MTP, and compatible Build Profile selections; global `[*]` parameters use a separate modal. The list identifies whether each preset is served by the active profile. Primary and companion availability is indicated with tooltip icons and badges.
- **Build Profile UI:** The Build Profiles panel creates, duplicates, edits, deletes, activates, and builds profiles. Deleting configuration deliberately leaves the checkout on disk. Hardware templates populate the selected profile only.
- **Template variables:** `{{ version }}` and `{{ llama_server_url }}` are injected by Flask.
- **No frameworks:** Vanilla JS only. DOM manipulation via `getElementById` / `innerHTML`.

---

## Container Details

- **Base image:** `debian:bookworm-slim` with `contrib`, `non-free`, and `non-free-firmware` enabled for CUDA build packages
- **User:** `llama` (non-root with passwordless `sudo` restricted to `apt-get` for build-preset packages, for rootless Podman `userns_mode: keep-id`)
- **Workdir:** `/home/llama/app`
- **Ports:** `5000` (Flask UI), `8080` (llama-server)
- **Environment variables:**
  - `LLAMA_CONTROLLER_PORT`: Web UI port (default `5000`)
  - `LLAMA_SERVER_PORT`: llama-server port (default `8080`)
  - `LLAMA_SERVER_URL`: Public link to llama-server shown in UI (default `http://localhost:8080`)
- **Volume mount:** `./data → /home/llama/app/data` (`:Z` for SELinux)
- **Optional profile mount:** `./build-profiles → /home/llama/app/build-profiles` (`:Z` for SELinux), analogous to the optional `venv` mount.
- **Entrypoint:** `./venv/bin/python server/server.py`
- **Log files:** `/tmp/llama.logs` (server), `/tmp/build.logs` (build) — ephemeral, not persisted; repetitive 200/304 GET access logs for UI polling endpoints (`/api/status`, `/api/logs`, `/api/build-logs`, `/api/models`, `/api/model-presets`) are suppressed to keep container logs clean

---

## Maintenance Directives

* **File Modifications:** Keep changes strictly scoped to the user's request.
* **Podman Compatibility:** Ensure all Dockerfile and compose changes remain compatible with rootless Podman (e.g., preserving `userns_mode: keep-id` and `:Z` volume mounts).
* **Build Profiles:** Never delete, repoint, or reuse another profile’s checkout when adding or building a repository. Profile trees live outside `data/` under `build-profiles/`; mount that directory optionally for persistence, analogous to `venv/`. The active profile alone controls startup and restarts; inactive profiles remain independently buildable. Presets may be assigned to one or more compatible profile IDs, and only those assigned to the active profile are passed to `llama-server`.
* **Model Handling:** GGUF files >2GB are typically volume-mounted by the user or downloaded directly from Hugging Face via the web UI (which supports SSE streaming). Primary upload/download opens a prefilled preset modal without changing the server. `mmproj-*` and `mtp-*` companions are attached to an existing preset. Deleting a primary removes matching presets; deleting a companion detaches matching `mmproj`, `spec-draft-model`, or legacy `model-draft` references. Restart only when preset configuration changed.
* **Signal Handling:** `server.py` traps `SIGTERM`/`SIGINT` to gracefully stop the child `llama-server` process before exiting.
* **Documentation:** Whenever features are added, modified, or removed, you MUST update both `README.md`, `DOCKERHUB.md` and `AGENTS.md` to capture these changes (e.g. updating API route tables, feature lists, or system architecture).

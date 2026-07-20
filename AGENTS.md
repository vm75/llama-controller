# Llama Controller — Agent Reference

A lightweight, containerized control plane for `llama.cpp`. Flask backend + vanilla HTML/JS/Tailwind frontend. Manages building `llama.cpp` from source, model files, native `models.ini` serving presets, and the `llama-server` process lifecycle.

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
└── models/                        # GGUF model files
llama.cpp/                         # Cloned at build time by server.py
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
│  llama-server :8080        (built from llama.cpp/build/bin/)       │
│    └── started/stopped by Flask via subprocess                     │
│                                                                    │
│  data/ (volume mount)                                              │
│    ├── config.json         (runtime config, persisted)             │
│    ├── models.ini          (native model serving presets)          │
│    └── models/             (GGUF files)                            │
└────────────────────────────────────────────────────────────────────┘
```

**Startup sequence:** `server.py` → `ensure_data_dir()` (seeds config and models preset files if missing) → `start_llama()` (auto-start if binary exists) → `app.run(:5000)`.

**Serving flow:** `start_llama()` launches `llama-server --models-preset data/models.ini --host 0.0.0.0 --port 8080`. Uploads and downloads only open a prefilled preset modal; they do not restart the process. Deleting a model removes presets that reference it and restarts only when at least one preset was removed. Saving `models.ini` or refreshing presets through the UI also restarts it. Individual presets can be duplicated with an auto-generated unique running number suffix.

**Build flow:** The UI build preset saves its CMake flags and required apt packages to `config.json`. `POST /api/build` installs that saved package list via `sudo apt-get`, clones from `LLAMA_CPP_REPO` (default `https://github.com/ggml-org/llama.cpp.git`, re-clones if origin changed), checks out `LLAMA_CPP_BRANCH` (default `master`), runs `cmake -B build -DCMAKE_BUILD_TYPE=Release {cmake_params}`, builds the `llama-server` target, and auto-starts it. Packages are configured only through the UI/config; there is no Docker environment override.

---

## config.json Schema

```jsonc
{
  "cmake_params": "-DGGML_NATIVE=ON ...",        // Extra CMake flags (appended after -DCMAKE_BUILD_TYPE=Release)
  "extra_packages": "",                         // Additional apt packages to install before build (empty by default)
  "cmake_presets": [               // UI dropdown presets for cmake_params and extra_packages
    { "label": "CPU",          "flags": "-DGGML_NATIVE=ON ...", "extra_packages": "" },
    { "label": "CPU-BLAS",     "flags": "-DGGML_BLAS=ON ...",   "extra_packages": "libopenblas-dev" },
    { "label": "NVIDIA CUDA",  "flags": "-DGGML_CUDA=ON ...",   "extra_packages": "nvidia-cuda-toolkit" },
    { "label": "AMD ROCm",     "flags": "-DGGML_HIPBLAS=ON ...", "extra_packages": "hipcc" },
    { "label": "Vulkan",       "flags": "-DGGML_VULKAN=ON ...", "extra_packages": "libvulkan-dev" }
  ],
  "llama_server_url": "http://localhost:8080"    // Configurable link shown in UI when server is running
}
```

`config.json` contains build/UI configuration only. Legacy `params` arrays are migrated to `models.ini` when that file is first created.

## models.ini Schema

```ini
[*]                              # Global parameters inherited by every preset
threads = 4
ctx-size = 8192

[coding-model]                   # Preset name exposed by the router
model = data/models/model.gguf   # Path relative to the app root, or an absolute path
n-gpu-layers = 99
temp = 0.2
```

Parameter keys match `llama-server` flags without leading dashes. The UI allows missing model paths so stale presets can be edited or deleted; `GET /api/model-presets` returns `model_exists` for each preset. Writes normalize the file and do not preserve comments.

**Hardcoded flags** (not editable in config): `--models-preset data/models.ini`, `--host 0.0.0.0`, `--port 8080`.

---

## API Routes

All routes are defined in `server/server.py`. All API responses are JSON.

| Method | Path | Purpose | Side Effects |
|---|---|---|---|
| `GET` | `/` | Serve the single-page UI | — |
| `GET` | `/api/status` | `{ binary_built, server_running }` | — |
| `GET` | `/api/config` | Return full config.json | — |
| `POST` | `/api/config` | Save build/UI config | Writes config.json (no restart) |
| `POST` | `/api/server-url` | Update the `llama_server_url` field | Writes config.json (no restart) |
| `GET` | `/api/models` | List model files with sizes | — |
| `POST` | `/api/upload` | Upload a model file | Saves to models/ (UI opens prefilled preset modal; no restart) |
| `POST` | `/api/download` | Download a model from HF | Saves to models/ (SSE; UI opens prefilled preset modal; no restart) |
| `DELETE` | `/api/models/<filename>` | Delete a model file | Removes matching presets; restarts only if presets changed |
| `GET` | `/api/model-presets` | Return global parameters and serving presets | Reads models.ini |
| `PUT` | `/api/model-presets` | Replace global parameters and presets | Writes models.ini, restarts process if built |
| `POST` | `/api/model-presets/refresh` | Auto-add GGUF presets & remove stale presets | Writes models.ini, restarts process if changed |
| `POST` | `/api/build` | Clone/update llama.cpp and build | Long-running, writes build logs, auto-starts |
| `GET` | `/api/logs` | Last 100 lines of server logs | — |
| `GET` | `/api/build-logs` | Last 100 lines of build logs | — |
| `GET` | `/api/llama-help` | `llama-server --help` output | — |

---

## Frontend Patterns

- **Single file:** `server/templates/index.html` — Jinja2 template with inline `<script>`.
- **Styling:** Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com">`). No build step.
- **Polling:** Status is polled every 3s; logs every 2s; model files and presets every 10s via `setInterval` + `fetch`.
- **Preset UI:** Serving presets are listed in their own panel. Add/edit uses a modal, global `[*]` parameters use a separate modal, and model availability is indicated with tooltip icons.
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
  - `LLAMA_CPP_REPO`: `llama.cpp` Git repository URL (default `https://github.com/ggml-org/llama.cpp.git`)
  - `LLAMA_CPP_BRANCH`: `llama.cpp` target branch (default `master`)
- **Volume mount:** `./data → /home/llama/app/data` (`:Z` for SELinux)
- **Entrypoint:** `./venv/bin/python server/server.py`
- **Log files:** `/tmp/llama.logs` (server), `/tmp/build.logs` (build) — ephemeral, not persisted

---

## Maintenance Directives

* **File Modifications:** Keep changes strictly scoped to the user's request.
* **Podman Compatibility:** Ensure all Dockerfile and compose changes remain compatible with rootless Podman (e.g., preserving `userns_mode: keep-id` and `:Z` volume mounts).
* **Model Handling:** Models >2GB are typically volume-mounted by the user or downloaded directly from Hugging Face via the web UI (which supports SSE streaming). Upload/download opens a prefilled preset modal without changing the server; deleting a file removes presets that reference it and then restarts the server.
* **Signal Handling:** `server.py` traps `SIGTERM`/`SIGINT` to gracefully stop the child `llama-server` process before exiting.
* **Documentation:** Whenever features are added, modified, or removed, you MUST update both `README.md` and `AGENTS.md` to capture these changes (e.g. updating API route tables, feature lists, or system architecture).

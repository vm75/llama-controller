# Llama Web UI — Agent Reference

A lightweight, containerized control plane for `llama.cpp`. Flask backend + vanilla HTML/JS/Tailwind frontend. Manages building `llama.cpp` from source, model uploads, and the `llama-server` process lifecycle via a JSON config file.

## Core Principles: YAGNI & KISS

### KISS (Keep It Simple, Stupid)
* **No Build Steps for Frontend:** The UI is a single `server/templates/index.html` using Tailwind CSS via CDN. Do not introduce Node.js, npm, Webpack, Vite, React, Vue, or any frontend frameworks.
* **Minimal Backend:** The backend is a single `server/server.py` using Flask. Do not split into multiple modules.
* **Process Management:** We use Python `subprocess` to manage `llama-server`. Do not introduce supervisor, systemd, or Celery.

### YAGNI (You Aren't Gonna Need It)
* **No Databases:** State lives entirely in `config.json`. No SQLite, PostgreSQL, Redis, etc.
* **No User Authentication:** This is a local/private tool. No login screens, JWT, or sessions.
* **No Bloatware:** Only add dependencies to `requirements.txt` if absolutely necessary.

---

## Project Layout

```
llama-web-ui/
├── server/                        # All backend + frontend code
│   ├── server.py                  # Flask app — the entire backend (274 lines)
│   ├── requirements.txt           # Python deps: Flask, Werkzeug
│   ├── static/
│   │   └── favicon.svg            # App favicon
│   └── templates/
│       └── index.html             # Single-page UI (Jinja2 + Tailwind CDN + vanilla JS)
├── config.default.json            # Default config — seeded into data/config.json on first run
├── Dockerfile                     # Debian bookworm-slim, non-root `llama` user, venv
├── docker-compose.yml.sample      # Sample compose file (users copy to docker-compose.yml)
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
└── models/                        # GGUF model files
llama.cpp/                         # Cloned at build time by server.py
venv/                              # Python virtual environment (created by Dockerfile)
docker-compose.yml                 # User's local compose (copied from .sample)
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
│    └── models/             (GGUF files)                            │
└────────────────────────────────────────────────────────────────────┘
```

**Startup sequence:** `server.py` → `ensure_data_dir()` (seeds config if missing) → `start_llama()` (auto-start if binary exists) → `app.run(:5000)`.

**Build flow:** `POST /api/build` → `git clone` / `git fetch+reset` → `cmake -B build -DCMAKE_BUILD_TYPE=Release {cmake_params}` → `cmake --build build --target llama-server -j` → auto-start. Note: llama.cpp uses `master` branch, not `main`.

---

## config.json Schema

```jsonc
{
  "params": [                      // Array of llama-server CLI flags
    { "flag": "--threads", "value": "4" },       // flag + value → "--threads 4"
    { "flag": "--jinja",  "value": "" }          // empty value → boolean flag "--jinja"
  ],
  "cmake_params": "-DGGML_NATIVE=ON ...",        // Extra CMake flags (appended after -DCMAKE_BUILD_TYPE=Release)
  "cmake_presets": [               // UI dropdown presets for cmake_params
    { "label": "CPU",          "flags": "-DGGML_NATIVE=ON -DGGML_LTO=ON -DBUILD_SHARED_LIBS=OFF" },
    { "label": "NVIDIA CUDA",  "flags": "-DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF" },
    { "label": "AMD ROCm",     "flags": "-DGGML_HIPBLAS=ON -DBUILD_SHARED_LIBS=OFF" },
    { "label": "Vulkan",       "flags": "-DGGML_VULKAN=ON -DBUILD_SHARED_LIBS=OFF" }
  ],
  "llama_server_url": "http://localhost:8080"    // Configurable link shown in UI when server is running
}
```

**Hardcoded flags** (not in config, always passed): `--models-dir`, `--host 0.0.0.0`, `--port 8080`.

---

## API Routes

All routes are defined in `server/server.py`. All API responses are JSON.

| Method | Path | Purpose | Side Effects |
|---|---|---|---|
| `GET` | `/` | Serve the single-page UI | — |
| `GET` | `/api/status` | `{ binary_built, server_running }` | — |
| `GET` | `/api/config` | Return full config.json | — |
| `POST` | `/api/config` | Save config and restart llama-server | Writes config.json, restarts process |
| `POST` | `/api/server-url` | Update the `llama_server_url` field | Writes config.json (no restart) |
| `GET` | `/api/models` | List model files with sizes | — |
| `POST` | `/api/upload` | Upload a model file | Saves to models/, restarts server |
| `POST` | `/api/download` | Download a model from HF | Saves to models/, restarts server (SSE stream) |
| `DELETE` | `/api/models/<filename>` | Delete a model file | Removes file, restarts server |
| `POST` | `/api/build` | Clone/update llama.cpp and build | Long-running, writes build logs, auto-starts |
| `GET` | `/api/logs` | Last 100 lines of server logs | — |
| `GET` | `/api/build-logs` | Last 100 lines of build logs | — |
| `GET` | `/api/llama-help` | `llama-server --help` output | — |

---

## Frontend Patterns

- **Single file:** `server/templates/index.html` — Jinja2 template with inline `<script>`.
- **Styling:** Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com">`). No build step.
- **Polling:** Status polled every 3s, logs every 2s, models every 10s via `setInterval` + `fetch`.
- **Template variables:** `{{ version }}` and `{{ llama_server_url }}` are injected by Flask.
- **No frameworks:** Vanilla JS only. DOM manipulation via `getElementById` / `innerHTML`.

---

## Container Details

- **Base image:** `debian:bookworm-slim`
- **User:** `llama` (non-root, for rootless Podman `userns_mode: keep-id`)
- **Workdir:** `/home/llama/app`
- **Ports:** `5000` (Flask UI), `8080` (llama-server)
- **Volume mount:** `./data → /home/llama/app/data` (`:Z` for SELinux)
- **Entrypoint:** `./venv/bin/python server/server.py`
- **Log files:** `/tmp/llama.logs` (server), `/tmp/build.logs` (build) — ephemeral, not persisted

---

## Maintenance Directives

* **File Modifications:** Keep changes strictly scoped to the user's request.
* **Podman Compatibility:** Ensure all Dockerfile and compose changes remain compatible with rootless Podman (e.g., preserving `userns_mode: keep-id` and `:Z` volume mounts).
* **Model Handling:** Models >2GB are typically volume-mounted by the user or downloaded directly from Hugging Face via the web UI (which supports SSE streaming). Keep upload/download logic straightforward.
* **Signal Handling:** `server.py` traps `SIGTERM`/`SIGINT` to gracefully stop the child `llama-server` process before exiting.
* **Documentation:** Whenever features are added, modified, or removed, you MUST update both `README.md` and `AGENTS.md` to capture these changes (e.g. updating API route tables, feature lists, or system architecture).
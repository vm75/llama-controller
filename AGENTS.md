This project is a lightweight control plane for `llama.cpp`. It runs inside a rootless Podman container and uses a Python Flask backend paired with a vanilla HTML/JS/Tailwind frontend. It manages compiling `llama.cpp` from source, model uploads, and the process lifecycle of `llama-server` based on a JSON configuration file.

## Core Principles: YAGNI & KISS

### 1. KISS (Keep It Simple, Stupid)
* **No Build Steps for Frontend:** The UI is a single `templates/index.html` file using Tailwind CSS via CDN. Do not introduce Node.js, npm, Webpack, Vite, React, Vue, or any frontend frameworks.
* **Minimal Backend:** The backend is a single `server.py` using Flask. Do not split this into multiple modules or add unnecessary routing complexities.
* **Process Management:** We use basic Python `subprocess` to manage `llama-server`. Do not introduce supervisor, systemd (inside the container), or Celery.

### 2. YAGNI (You Aren't Gonna Need It)
* **No Databases:** State is maintained entirely in a simple `config.json` file. Do not suggest or implement SQLite, PostgreSQL, Redis, or any other database engine.
* **No User Authentication:** This is a local/private tool. Do not add login screens, JWT tokens, or session management.
* **No Bloatware:** Only add dependencies to `requirements.txt` if absolutely necessary to achieve a stated goal.

## Maintenance Directives
* **File Modifications:** Keep changes strictly scoped to the user's request.
* **Podman Compatibility:** Ensure all Dockerfile and compose changes remain compatible with rootless Podman (e.g., preserving `userns_mode: keep-id` and `:Z` volume mounts).
* **Model Handling:** Recognize that models >2GB are typically volume-mounted by the user rather than uploaded via the web UI. Keep upload logic straightforward and functional.
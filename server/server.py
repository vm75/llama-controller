import os
import sys
import json
import signal
import subprocess
import urllib.request
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, stream_with_context

# server.py lives in server/; the app root is one level up.
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Persistent data directory – the single volume mount point.
DATA_DIR = os.path.join(APP_ROOT, 'data')
# Baked-in default config shipped inside the image.
DEFAULT_CONFIG = os.path.join(APP_ROOT, 'config.default.json')

try:
  with open(os.path.join(APP_ROOT, 'VERSION'), 'r') as f:
    __version__ = f.read().strip()
except FileNotFoundError:
  __version__ = 'dev'

app = Flask(__name__)
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
MODELS_DIR = os.path.join(DATA_DIR, 'models')
LOG_FILE = '/tmp/llama.logs'
BUILD_LOG_FILE = '/tmp/build.logs'
LLAMA_REPO = os.path.join(APP_ROOT, 'llama.cpp')

def ensure_data_dir():
  """Create data/ and models/ dirs; seed config.json from default if absent."""
  import shutil
  os.makedirs(MODELS_DIR, exist_ok=True)
  if not os.path.exists(CONFIG_FILE):
    shutil.copy2(DEFAULT_CONFIG, CONFIG_FILE)

llama_process = None

def load_config():
  with open(CONFIG_FILE, 'r') as f:
    return json.load(f)

def save_config(data):
  with open(CONFIG_FILE, 'w') as f:
    json.dump(data, f, indent=4)

def start_llama():
  global llama_process
  stop_llama()

  config = load_config()
  server_bin = os.path.join(LLAMA_REPO, 'build', 'bin', 'llama-server')

  if not os.path.exists(server_bin):
    return False, "llama-server binary not found. Please build it first."

  cmd = [
    server_bin,
    "--models-dir", MODELS_DIR,
    "--host", "0.0.0.0",
    "--port", "8080",
  ]

  for entry in config.get('params', []):
    flag = entry.get('flag', '').strip()
    value = entry.get('value', '').strip()
    if flag:
      cmd.append(flag)
      if value:
        cmd.append(value)

  # Environment variables
  env = os.environ.copy()
  env["LLAMA_CACHE"] = MODELS_DIR

  log_file = open(LOG_FILE, 'w')
  llama_process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, env=env)
  return True, "Started successfully"

def stop_llama():
  global llama_process
  if llama_process and llama_process.poll() is None:
    llama_process.terminate()
    llama_process.wait()
  else:
    # Fallback to pkill just in case
    subprocess.run(["pkill", "-9", "llama-server"], stderr=subprocess.DEVNULL)

@app.route('/')
def index():
  config = load_config()
  llama_server_url = config.get('llama_server_url', 'http://localhost:8080')
  return render_template('index.html', version=__version__, llama_server_url=llama_server_url)

@app.route('/favicon.ico')
def favicon():
  return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.svg', mimetype='image/svg+xml')

@app.route('/llama-icon.svg')
def llama_icon():
  icon_path = os.path.join(LLAMA_REPO, 'build', 'tools', 'ui', 'dist', 'favicon.svg')
  if os.path.exists(icon_path):
    return send_from_directory(os.path.join(app.root_path, LLAMA_REPO, 'build', 'tools', 'ui', 'dist'), 'favicon.svg', mimetype='image/svg+xml')
  # Fallback: serve favicon if llama.cpp hasn't been cloned yet
  return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.svg', mimetype='image/svg+xml')

@app.route('/api/config', methods=['GET'])
def get_config():
  return jsonify(load_config())

@app.route('/api/llama-help')
def llama_help():
  server_bin = os.path.join(LLAMA_REPO, 'build', 'bin', 'llama-server')
  if not os.path.exists(server_bin):
    return jsonify({"help": "(Binary not built yet — click Build Latest first.)"})
  result = subprocess.run(
    [server_bin, '--help'],
    capture_output=True, text=True, timeout=10
  )
  # llama-server prints help to stderr with exit code 1, stdout may be empty
  output = result.stdout or result.stderr
  return jsonify({"help": output.strip()})

@app.route('/api/config', methods=['POST'])
def update_config():
  data = request.json
  # Expect { "params": [...], "cmake_params": "...", "cmake_presets": [...] }
  if 'params' not in data:
    return jsonify({"success": False, "message": "Invalid config: missing 'params' key"})
  # Preserve keys not explicitly sent; fall back to existing config values if absent
  existing = load_config()
  if 'cmake_params' not in data:
    data['cmake_params'] = existing.get('cmake_params', '')
  if 'cmake_presets' not in data:
    data['cmake_presets'] = existing.get('cmake_presets', [])
  if 'llama_server_url' not in data:
    data['llama_server_url'] = existing.get('llama_server_url', 'http://localhost:8080')
  save_config(data)
  success, msg = start_llama()
  return jsonify({"success": success, "message": msg})

@app.route('/api/server-url', methods=['POST'])
def set_server_url():
  """Update the configurable llama-server link URL without restarting."""
  data = request.json
  url = (data.get('url') or '').strip()
  if not url:
    return jsonify({"success": False, "message": "URL cannot be empty"})
  config = load_config()
  config['llama_server_url'] = url
  save_config(config)
  return jsonify({"success": True, "message": "URL saved"})

@app.route('/api/status')
def get_status():
  server_bin = os.path.join(LLAMA_REPO, 'build', 'bin', 'llama-server')
  binary_built = os.path.exists(server_bin)
  server_running = llama_process is not None and llama_process.poll() is None
  return jsonify({"binary_built": binary_built, "server_running": server_running})

@app.route('/api/build', methods=['POST'])
def build_llama():
  try:
    # If the directory exists but is not a valid git repo (e.g. corrupted clone),
    # wipe it so we can do a clean clone below.
    if os.path.exists(LLAMA_REPO) and not os.path.isdir(os.path.join(LLAMA_REPO, '.git')):
      import shutil
      shutil.rmtree(LLAMA_REPO)

    build_log = open(BUILD_LOG_FILE, 'w')

    if not os.path.exists(LLAMA_REPO):
      result = subprocess.run(
        ["git", "clone", "https://github.com/ggerganov/llama.cpp.git"],
        stdout=build_log, stderr=subprocess.STDOUT
      )
      build_log.flush()
      if result.returncode != 0:
        build_log.close()
        raise subprocess.CalledProcessError(result.returncode, 'git clone')

    # Fetch latest master, hard-reset, then build only the llama-server target.
    # NOTE: ggerganov/llama.cpp uses 'master' as its default branch, not 'main'.
    config = load_config()
    extra_cmake = config.get('cmake_params', '').strip()
    cmake_flags = "-DCMAKE_BUILD_TYPE=Release"
    if extra_cmake:
      cmake_flags += " " + extra_cmake
    build_cmd = (
      f"cd {LLAMA_REPO} && "
      "git fetch origin master && "
      "git reset --hard origin/master && "
      f"cmake -B build {cmake_flags} && "
      "cmake --build build --target llama-server -j"
    )
    result = subprocess.run(
      build_cmd, shell=True, executable='/bin/bash',
      stdout=build_log, stderr=subprocess.STDOUT
    )
    build_log.close()
    if result.returncode != 0:
      raise subprocess.CalledProcessError(result.returncode, build_cmd)

    # Auto-start after successful build
    start_llama()
    return jsonify({"success": True, "message": "Build successful and restarted."})
  except subprocess.CalledProcessError as e:
    msg = f"Build failed (exit {e.returncode}). See Build Logs tab for details."
    return jsonify({"success": False, "message": msg})

@app.route('/api/models')
def list_models():
  try:
    files = sorted(os.listdir(MODELS_DIR))
  except FileNotFoundError:
    files = []
  models = []
  for f in files:
    path = os.path.join(MODELS_DIR, f)
    if os.path.isfile(path):
      models.append({"name": f, "size": os.path.getsize(path)})
  return jsonify({"models": models})

@app.route('/api/upload', methods=['POST'])
def upload_model():
  if 'file' not in request.files:
    return jsonify({"success": False, "message": "No file part"})
  file = request.files['file']
  if file.filename == '':
    return jsonify({"success": False, "message": "No selected file"})

  file.save(os.path.join(MODELS_DIR, file.filename))
  # Restart llama-server so the new model is immediately available
  start_llama()
  return jsonify({"success": True, "message": f"Uploaded {file.filename} and restarted server."})

@app.route('/api/models/<path:filename>', methods=['DELETE'])
def delete_model(filename):
  # Prevent path traversal
  safe_path = os.path.join(MODELS_DIR, os.path.basename(filename))
  if not os.path.isfile(safe_path):
    return jsonify({"success": False, "message": "File not found"}), 404
  os.remove(safe_path)
  start_llama()
  return jsonify({"success": True, "message": f"Deleted {filename} and restarted server."})

@app.route('/api/download', methods=['POST'])
def download_model():
  """Stream a GGUF model download from Hugging Face with SSE progress events."""
  body = request.get_json(force=True)
  repo = (body.get('repo') or '').strip()
  filename = (body.get('filename') or '').strip()
  if not repo or not filename:
    return jsonify({"success": False, "message": "Both 'repo' and 'filename' are required."}), 400
  if not filename.lower().endswith('.gguf'):
    return jsonify({"success": False, "message": "Only .gguf files are supported."}), 400
  safe_name = os.path.basename(filename)
  dest = os.path.join(MODELS_DIR, safe_name)
  url = f"https://huggingface.co/{repo}/resolve/main/{filename}"

  def generate():
    try:
      req = urllib.request.urlopen(url)
      total = int(req.headers.get('Content-Length') or 0)
      downloaded = 0
      chunk_size = 512 * 1024  # 512 KB
      with open(dest, 'wb') as f:
        while True:
          chunk = req.read(chunk_size)
          if not chunk:
            break
          f.write(chunk)
          downloaded += len(chunk)
          pct = int(downloaded * 100 / total) if total else 0
          yield f"data: {json.dumps({'progress': pct, 'downloaded': downloaded, 'total': total})}\n\n"
      start_llama()
      yield f"data: {json.dumps({'success': True, 'message': f'Downloaded {safe_name} and restarted server.'})}\n\n"
    except Exception as e:
      if os.path.exists(dest):
        os.remove(dest)
      yield f"data: {json.dumps({'success': False, 'message': f'Download failed: {e}'})}\n\n"

  return Response(
    stream_with_context(generate()),
    mimetype='text/event-stream',
    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
  )

@app.route('/api/logs')
def get_logs():
  if not os.path.exists(LOG_FILE):
    return jsonify({"logs": ""})
  with open(LOG_FILE, 'r') as f:
    lines = f.readlines()[-100:]
    return jsonify({"logs": "".join(lines)})

@app.route('/api/build-logs')
def get_build_logs():
  if not os.path.exists(BUILD_LOG_FILE):
    return jsonify({"logs": ""})
  with open(BUILD_LOG_FILE, 'r') as f:
    lines = f.readlines()[-100:]
    return jsonify({"logs": "".join(lines)})

def _shutdown(signum, frame):
  """Gracefully stop the child llama-server and exit when podman sends SIGTERM."""
  stop_llama()
  sys.exit(0)

if __name__ == '__main__':
  signal.signal(signal.SIGTERM, _shutdown)
  signal.signal(signal.SIGINT, _shutdown)
  # Ensure data directory structure and seed config on first run
  ensure_data_dir()
  # Always start on docker start if binary exists
  start_llama()
  # Run the control web server
  app.run(host='0.0.0.0', port=5000)
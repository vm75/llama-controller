import os
import sys
import json
import re
import shlex
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
DEFAULT_MODELS_PRESET = os.path.join(APP_ROOT, 'models.ini.example')

try:
  with open(os.path.join(APP_ROOT, 'VERSION'), 'r') as f:
    __version__ = f.read().strip()
except FileNotFoundError:
  __version__ = 'dev'

app = Flask(__name__)
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
MODELS_PRESET_FILE = os.path.join(DATA_DIR, 'models.ini')
MODELS_DIR = os.path.join(DATA_DIR, 'models')
LOG_FILE = '/tmp/llama.logs'
BUILD_LOG_FILE = '/tmp/build.logs'
LLAMA_REPO = os.path.join(APP_ROOT, 'llama.cpp')

def ensure_data_dir():
  """Create runtime directories and seed persistent configuration files."""
  import shutil
  os.makedirs(MODELS_DIR, exist_ok=True)
  if not os.path.exists(CONFIG_FILE):
    shutil.copy2(DEFAULT_CONFIG, CONFIG_FILE)
  if not os.path.exists(MODELS_PRESET_FILE):
    config = load_config()
    legacy_params = config.pop('params', [])
    if legacy_params:
      global_params = []
      for entry in legacy_params:
        key = (entry.get('flag') or '').strip().lstrip('-')
        value = (entry.get('value') or '').strip() or 'true'
        if key:
          global_params.append({'key': key, 'value': value})
      save_model_presets(global_params, [])
      save_config(config)
    else:
      shutil.copy2(DEFAULT_MODELS_PRESET, MODELS_PRESET_FILE)

llama_process = None

def load_config():
  with open(CONFIG_FILE, 'r') as f:
    return json.load(f)

def save_config(data):
  with open(CONFIG_FILE, 'w') as f:
    json.dump(data, f, indent=4)

def load_model_presets():
  """Parse the small INI subset used by llama-server model presets."""
  version = '1'
  sections = {}
  current_section = None
  with open(MODELS_PRESET_FILE, 'r') as f:
    for line_number, raw_line in enumerate(f, 1):
      line = raw_line.strip()
      if not line or line.startswith(('#', ';')):
        continue
      if line.startswith('[') and line.endswith(']'):
        current_section = line[1:-1].strip()
        if not current_section:
          raise ValueError(f'Empty section name on line {line_number}')
        sections.setdefault(current_section, {})
        continue
      if '=' not in line:
        raise ValueError(f'Expected key = value on line {line_number}')
      key, value = (part.strip() for part in line.split('=', 1))
      if not key:
        raise ValueError(f'Empty key on line {line_number}')
      if current_section is None:
        if key != 'version':
          raise ValueError(f'Only version is allowed before a section (line {line_number})')
        version = value
      else:
        sections[current_section][key] = value

  global_params = _params_from_mapping(sections.pop('*', {}))
  presets = []
  for name, params in sections.items():
    model = params.get('model', '')
    presets.append({
      'name': name,
      'params': _params_from_mapping(params),
      'model_exists': _preset_model_exists(name, model),
    })
  return {'version': version, 'global_params': global_params, 'presets': presets}

def _params_from_mapping(mapping):
  return [{'key': key, 'value': value} for key, value in mapping.items()]

def _preset_model_exists(name, model):
  """Report local model availability without rejecting editable stale presets."""
  if model:
    return os.path.isfile(_resolved_model_path(model))
  return os.path.isfile(os.path.join(MODELS_DIR, os.path.basename(name)))

def _resolved_model_path(model):
  path = os.path.expanduser(model)
  if not os.path.isabs(path):
    path = os.path.join(APP_ROOT, path)
  return os.path.realpath(path)

def _validate_params(params):
  if not isinstance(params, list):
    raise ValueError('Parameters must be an array')
  normalized = []
  seen = set()
  for entry in params:
    if not isinstance(entry, dict):
      raise ValueError('Each parameter must contain a key and value')
    key = str(entry.get('key') or '').strip().lstrip('-')
    value = str(entry.get('value') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', key):
      raise ValueError(f'Invalid parameter name: {key or "(empty)"}')
    if key in seen:
      raise ValueError(f'Duplicate parameter: {key}')
    if '\n' in value or '\r' in value:
      raise ValueError(f'Parameter {key} must be a single line')
    seen.add(key)
    normalized.append({'key': key, 'value': value})
  return normalized

def save_model_presets(global_params, presets):
  """Validate and atomically rewrite data/models.ini."""
  global_params = _validate_params(global_params)
  if not isinstance(presets, list):
    raise ValueError('Presets must be an array')

  normalized_presets = []
  seen_names = set()
  for preset in presets:
    if not isinstance(preset, dict):
      raise ValueError('Each preset must contain a name and parameters')
    name = str(preset.get('name') or '').strip()
    if not name or name == '*' or any(char in name for char in '[]\r\n'):
      raise ValueError(f'Invalid preset name: {name or "(empty)"}')
    if name in seen_names:
      raise ValueError(f'Duplicate preset name: {name}')
    seen_names.add(name)
    normalized_presets.append({
      'name': name,
      'params': _validate_params(preset.get('params', [])),
    })

  lines = ['version = 1', '', '[*]']
  for entry in global_params:
    lines.append(f"{entry['key']} = {entry['value']}")
  for preset in normalized_presets:
    lines.extend(['', f"[{preset['name']}]"])
    for entry in preset['params']:
      lines.append(f"{entry['key']} = {entry['value']}")
  content = '\n'.join(lines) + '\n'
  temp_file = MODELS_PRESET_FILE + '.tmp'
  with open(temp_file, 'w') as f:
    f.write(content)
  os.replace(temp_file, MODELS_PRESET_FILE)

def start_llama():
  global llama_process
  stop_llama()

  server_bin = os.path.join(LLAMA_REPO, 'build', 'bin', 'llama-server')

  if not os.path.exists(server_bin):
    return False, "llama-server binary not found. Please build it first."

  cmd = [
    server_bin,
    "--models-preset", MODELS_PRESET_FILE,
    "--host", "0.0.0.0",
    "--port", "8080",
  ]

  log_file = open(LOG_FILE, 'w')
  llama_process = subprocess.Popen(
    cmd, stdout=log_file, stderr=subprocess.STDOUT, cwd=APP_ROOT
  )
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
  data = request.get_json(force=True)
  # Preserve keys not explicitly sent; fall back to existing config values if absent
  existing = load_config()
  if 'cmake_params' not in data:
    data['cmake_params'] = existing.get('cmake_params', '')
  if 'extra_packages' not in data:
    data['extra_packages'] = existing.get('extra_packages', '')
  if 'cmake_presets' not in data:
    data['cmake_presets'] = existing.get('cmake_presets', [])
  if 'llama_server_url' not in data:
    data['llama_server_url'] = existing.get('llama_server_url', 'http://localhost:8080')
  save_config(data)
  return jsonify({"success": True, "message": "Configuration saved."})

@app.route('/api/model-presets', methods=['GET'])
def get_model_presets():
  try:
    return jsonify(load_model_presets())
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Could not read models.ini: {e}"}), 500

@app.route('/api/model-presets', methods=['PUT'])
def update_model_presets():
  data = request.get_json(force=True)
  try:
    save_model_presets(data.get('global_params', []), data.get('presets', []))
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": str(e)}), 400
  restarted, message = start_llama()
  if restarted:
    message = "models.ini saved and llama-server restarted."
  else:
    message = f"models.ini saved. {message}"
  return jsonify({"success": True, "restarted": restarted, "message": message})

@app.route('/api/model-presets/refresh', methods=['POST'])
def refresh_model_presets():
  """Auto-add empty presets for GGUF files missing presets, and delete presets with missing model files."""
  try:
    preset_config = load_model_presets()
    global_params = preset_config['global_params']
    current_presets = preset_config['presets']
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Could not read models.ini: {e}"}), 500

  try:
    all_files = sorted(os.listdir(MODELS_DIR))
  except FileNotFoundError:
    all_files = []

  gguf_files = [f for f in all_files if f.lower().endswith('.gguf') and os.path.isfile(os.path.join(MODELS_DIR, f))]

  def get_preset_target_path(preset):
    model_val = next((param['value'] for param in preset['params'] if param['key'] == 'model'), '')
    if model_val:
      return _resolved_model_path(model_val)
    return os.path.realpath(os.path.join(MODELS_DIR, os.path.basename(preset['name'])))

  remaining_presets = []
  removed_count = 0
  for preset in current_presets:
    model_val = next((param['value'] for param in preset['params'] if param['key'] == 'model'), '')
    if _preset_model_exists(preset['name'], model_val):
      remaining_presets.append(preset)
    else:
      removed_count += 1

  seen_names = set(p['name'] for p in remaining_presets)
  covered_model_paths = set(get_preset_target_path(p) for p in remaining_presets)

  added_count = 0
  for f in gguf_files:
    file_path = os.path.realpath(os.path.join(MODELS_DIR, f))
    if file_path not in covered_model_paths:
      base_name = os.path.splitext(f)[0]
      name = base_name
      counter = 1
      while name in seen_names or name == '*':
        name = f"{base_name}-{counter}"
        counter += 1

      seen_names.add(name)
      remaining_presets.append({
        'name': name,
        'params': [{'key': 'model', 'value': f'data/models/{f}'}]
      })
      added_count += 1

  if added_count == 0 and removed_count == 0:
    return jsonify({
      "success": True,
      "restarted": False,
      "added_count": 0,
      "removed_count": 0,
      "message": "Presets up to date. No changes made."
    })

  try:
    save_model_presets(global_params, remaining_presets)
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Could not update models.ini: {e}"}), 500

  restarted, message = start_llama()
  restart_msg = "llama-server restarted." if restarted else message
  return jsonify({
    "success": True,
    "restarted": restarted,
    "added_count": added_count,
    "removed_count": removed_count,
    "message": f"Refreshed presets (added {added_count}, removed {removed_count}). {restart_msg}"
  })

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

def _normalize_git_url(url):
  if not url:
    return ''
  url = url.strip()
  if url.endswith('.git'):
    url = url[:-4]
  return url.rstrip('/')

def install_build_packages(log_file=None):
  config = load_config() if os.path.exists(CONFIG_FILE) else {}
  extra_packages = str(config.get('extra_packages') or '').strip()
  packages = shlex.split(extra_packages)
  if not packages:
    return
  output = log_file or subprocess.DEVNULL
  if log_file:
    log_file.write(f"Installing preset packages: {' '.join(packages)}\n")
    log_file.flush()
  subprocess.run(
    ["sudo", "apt-get", "update"],
    stdout=output, stderr=subprocess.STDOUT, check=True
  )
  subprocess.run(
    ["sudo", "apt-get", "install", "-y", "--no-install-recommends", "--", *packages],
    stdout=output, stderr=subprocess.STDOUT, check=True
  )

@app.route('/api/build', methods=['POST'])
def build_llama():
  try:
    repo_url = (os.environ.get('LLAMA_CPP_REPO') or 'https://github.com/ggml-org/llama.cpp.git').strip()
    branch = (os.environ.get('LLAMA_CPP_BRANCH') or 'master').strip()

    import shutil
    # If the directory exists but is not a valid git repo or points to a different origin,
    # wipe it so we can re-clone cleanly.
    if os.path.exists(LLAMA_REPO):
      if not os.path.isdir(os.path.join(LLAMA_REPO, '.git')):
        shutil.rmtree(LLAMA_REPO)
      else:
        origin_proc = subprocess.run(
          ["git", "remote", "get-url", "origin"],
          cwd=LLAMA_REPO, capture_output=True, text=True
        )
        current_origin = origin_proc.stdout.strip()
        if _normalize_git_url(current_origin) != _normalize_git_url(repo_url):
          shutil.rmtree(LLAMA_REPO)

    with open(BUILD_LOG_FILE, 'w') as build_log:
      install_build_packages(build_log)

      if not os.path.exists(LLAMA_REPO):
        result = subprocess.run(
          ["git", "clone", repo_url, "llama.cpp"],
          stdout=build_log, stderr=subprocess.STDOUT, cwd=APP_ROOT
        )
        if result.returncode != 0:
          raise subprocess.CalledProcessError(result.returncode, 'git clone')

      # Fetch the target branch and build only the llama-server target.
      config = load_config()
      extra_cmake = shlex.split(str(config.get('cmake_params') or ''))
      build_commands = [
        ["git", "fetch", "origin", branch],
        ["git", "checkout", "-B", branch, f"origin/{branch}"],
        ["git", "reset", "--hard", f"origin/{branch}"],
        ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release", *extra_cmake],
        ["cmake", "--build", "build", "--target", "llama-server", "-j"],
      ]
      for build_cmd in build_commands:
        result = subprocess.run(
          build_cmd, cwd=LLAMA_REPO,
          stdout=build_log, stderr=subprocess.STDOUT
        )
        if result.returncode != 0:
          raise subprocess.CalledProcessError(result.returncode, build_cmd)

    # Auto-start after successful build
    start_llama()
    return jsonify({"success": True, "message": "Build successful and restarted."})
  except subprocess.CalledProcessError as e:
    msg = f"Build failed (exit {e.returncode}). See Build Logs tab for details."
    return jsonify({"success": False, "message": msg})
  except ValueError as e:
    return jsonify({"success": False, "message": f"Invalid build settings: {e}"}), 400

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

  safe_name = os.path.basename(file.filename)
  file.save(os.path.join(MODELS_DIR, safe_name))
  return jsonify({
    "success": True,
    "filename": safe_name,
    "message": f"Uploaded {safe_name}. Configure its serving preset."
  })

@app.route('/api/models/<path:filename>', methods=['DELETE'])
def delete_model(filename):
  # Prevent path traversal and identify presets before removing the file.
  safe_path = os.path.join(MODELS_DIR, os.path.basename(filename))
  if not os.path.isfile(safe_path):
    return jsonify({"success": False, "message": "File not found"}), 404
  try:
    preset_config = load_model_presets()
    target_path = os.path.realpath(safe_path)
    remaining_presets = []
    removed_presets = []
    for preset in preset_config['presets']:
      model = next((param['value'] for param in preset['params'] if param['key'] == 'model'), '')
      implicit_model = not model and os.path.basename(preset['name']) == os.path.basename(filename)
      if (model and _resolved_model_path(model) == target_path) or implicit_model:
        removed_presets.append(preset['name'])
      else:
        remaining_presets.append(preset)
    # Validate before the model file is removed so a malformed hand-edited INI
    # cannot leave a model deleted without its matching presets updated.
    _validate_params(preset_config['global_params'])
    for preset in remaining_presets:
      _validate_params(preset['params'])
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Could not update models.ini: {e}"}), 400

  os.remove(safe_path)
  if not removed_presets:
    return jsonify({"success": True, "removed_presets": [], "message": f"Deleted {filename}."})

  try:
    save_model_presets(preset_config['global_params'], remaining_presets)
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Deleted {filename}, but could not save models.ini: {e}"}), 500
  restarted, message = start_llama()
  restart_message = "llama-server restarted." if restarted else message
  return jsonify({
    "success": True,
    "removed_presets": removed_presets,
    "restarted": restarted,
    "message": f"Deleted {filename} and removed {len(removed_presets)} serving preset(s). {restart_message}"
  })

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
      yield f"data: {json.dumps({'success': True, 'filename': safe_name, 'message': f'Downloaded {safe_name}. Configure its serving preset.'})}\n\n"
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

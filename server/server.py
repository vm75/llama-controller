import os
import sys
import json
import logging
import re
import shlex
import signal
import subprocess
import urllib.request
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, stream_with_context, send_file

class QuietPollingFilter(logging.Filter):
  """Filter out repetitive GET access logs for polling endpoints from Werkzeug logs."""
  POLL_ENDPOINTS = (
    '/api/status',
    '/api/logs',
    '/api/build-logs',
    '/api/models',
    '/api/model-presets',
  )

  def filter(self, record):
    msg = record.getMessage()
    if any(endpoint in msg for endpoint in self.POLL_ENDPOINTS):
      if ' 200 ' in msg or ' 304 ' in msg or msg.endswith(' 200 -') or msg.endswith(' 304 -'):
        return False
    return True

logging.getLogger('werkzeug').addFilter(QuietPollingFilter())

# server.py lives in server/; the app root is one level up.
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Load .env file if it exists (no external dependencies)
env_path = os.path.join(APP_ROOT, '.env')
if os.path.isfile(env_path):
  with open(env_path, 'r') as f:
    for line in f:
      line = line.strip()
      if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

# Persistent data directory – the single volume mount point.
DATA_DIR = os.environ.get('LLAMA_CONTROLLER_DATA_DIR', os.path.join(APP_ROOT, 'data'))
DATA_DIR = os.path.abspath(os.path.expanduser(DATA_DIR))
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
ACTIVE_MODELS_PRESET_FILE = '/tmp/llama-active-models.ini'
BUILD_PROFILES_DIR = os.environ.get('LLAMA_CONTROLLER_BUILD_PROFILES_DIR', os.path.join(APP_ROOT, 'build-profiles'))
BUILD_PROFILES_DIR = os.path.abspath(os.path.expanduser(BUILD_PROFILES_DIR))
DEFAULT_LLAMA_REPO_URL = 'https://github.com/ggml-org/llama.cpp.git'
PRESET_BUILD_PROFILE_COMMENT_PREFIX = '# llama-controller-build-profiles = '
COMPANION_PARAM_KEYS = ('mmproj', 'spec-draft-model', 'model-draft')

def ensure_data_dir():
  """Create runtime directories and seed persistent configuration files."""
  import shutil
  os.makedirs(MODELS_DIR, exist_ok=True)
  os.makedirs(BUILD_PROFILES_DIR, exist_ok=True)
  if not os.path.exists(CONFIG_FILE):
    shutil.copy2(DEFAULT_CONFIG, CONFIG_FILE)
  config = load_config()
  with open(CONFIG_FILE, 'r') as f:
    stored_config = json.load(f)
  if config != stored_config:
    save_config(config)
  if not os.path.exists(MODELS_PRESET_FILE):
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
    return _normalize_config(json.load(f))

def save_config(data):
  normalized = _normalize_config(data)
  temp_file = CONFIG_FILE + '.tmp'
  with open(temp_file, 'w') as f:
    json.dump(normalized, f, indent=4)
    f.write('\n')
  os.replace(temp_file, CONFIG_FILE)

def _normalize_config(data):
  """Validate config and migrate legacy single-build settings into a main profile."""
  if not isinstance(data, dict):
    raise ValueError('Configuration must be an object')

  normalized = dict(data)
  profiles = normalized.get('build_profiles')
  if not isinstance(profiles, list) or not profiles:
    profiles = [{
      'id': 'main',
      'name': 'Main llama.cpp',
      'repo_url': DEFAULT_LLAMA_REPO_URL,
      'branch': 'master',
      'cmake_params': str(normalized.get('cmake_params') or ''),
      'extra_packages': str(normalized.get('extra_packages') or ''),
    }]

  normalized_profiles = []
  seen_ids = set()
  for profile in profiles:
    if not isinstance(profile, dict):
      raise ValueError('Each build profile must be an object')
    profile_id = str(profile.get('id') or '').strip()
    name = str(profile.get('name') or '').strip()
    repo_url = str(profile.get('repo_url') or '').strip()
    branch = str(profile.get('branch') or '').strip()
    cmake_params = str(profile.get('cmake_params') or '').strip()
    extra_packages = str(profile.get('extra_packages') or '').strip()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', profile_id):
      raise ValueError(f'Invalid build profile id: {profile_id or "(empty)"}')
    if profile_id in seen_ids:
      raise ValueError(f'Duplicate build profile id: {profile_id}')
    if not name or any(char in name for char in '\r\n'):
      raise ValueError(f'Invalid build profile name: {name or "(empty)"}')
    if not repo_url or repo_url.startswith('-') or any(char.isspace() for char in repo_url):
      raise ValueError(f'Invalid repository URL for build profile {name}')
    if not branch or branch.startswith('-') or any(char.isspace() for char in branch):
      raise ValueError(f'Invalid branch for build profile {name}')
    seen_ids.add(profile_id)
    normalized_profiles.append({
      'id': profile_id,
      'name': name,
      'repo_url': repo_url,
      'branch': branch,
      'cmake_params': cmake_params,
      'extra_packages': extra_packages,
    })

  active_profile = str(normalized.get('active_build_profile') or normalized_profiles[0]['id']).strip()
  if active_profile not in seen_ids:
    raise ValueError('Active build profile does not exist')

  normalized['build_profiles'] = normalized_profiles
  normalized['active_build_profile'] = active_profile
  normalized.pop('cmake_params', None)
  normalized.pop('extra_packages', None)
  return normalized

def _get_build_profile(profile_id=None, config=None):
  config = config or load_config()
  target_id = profile_id or config['active_build_profile']
  return next((profile for profile in config['build_profiles'] if profile['id'] == target_id), None)

def _profile_repo_path(profile):
  return os.path.join(BUILD_PROFILES_DIR, profile['id'], 'llama.cpp')

def _profile_server_bin(profile):
  return os.path.join(_profile_repo_path(profile), 'build', 'bin', 'llama-server')

def _active_build_profile():
  config = load_config()
  return _get_build_profile(config=config)

def load_model_presets():
  """Parse the small INI subset used by llama-server model presets."""
  version = '1'
  sections = {}
  profile_assignments = {}
  current_section = None
  pending_build_profiles = None
  with open(MODELS_PRESET_FILE, 'r') as f:
    for line_number, raw_line in enumerate(f, 1):
      line = raw_line.strip()
      if not line:
        continue
      if line.startswith(('#', ';')):
        if line.startswith(PRESET_BUILD_PROFILE_COMMENT_PREFIX):
          pending_build_profiles = _parse_preset_build_profiles(
            line[len(PRESET_BUILD_PROFILE_COMMENT_PREFIX):], line_number
          )
        continue
      if line.startswith('[') and line.endswith(']'):
        current_section = line[1:-1].strip()
        if not current_section:
          raise ValueError(f'Empty section name on line {line_number}')
        sections.setdefault(current_section, {})
        if current_section != '*' and pending_build_profiles is not None:
          profile_assignments[current_section] = pending_build_profiles
        pending_build_profiles = None
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
  active_profile = _active_build_profile()
  presets = []
  for name, params in sections.items():
    model = params.get('model', '')
    mmproj = params.get('mmproj', '')
    mtp = params.get('spec-draft-model', '') or params.get('model-draft', '')
    build_profiles = profile_assignments.get(name)
    presets.append({
      'name': name,
      'params': _params_from_mapping(params),
      'build_profiles': build_profiles,
      'active_profile_supported': _preset_supports_build_profile(build_profiles, active_profile['id']),
      'model_exists': _preset_model_exists(name, model),
      'mmproj_exists': _optional_model_file_exists(mmproj),
      'mtp_exists': _optional_model_file_exists(mtp),
    })
  return {
    'version': version,
    'active_profile': {'id': active_profile['id'], 'name': active_profile['name']},
    'global_params': global_params,
    'presets': presets,
  }

def _parse_preset_build_profiles(value, line_number):
  profile_ids = [profile_id.strip() for profile_id in value.split(',') if profile_id.strip()]
  if not profile_ids:
    raise ValueError(f'Expected one or more build profile IDs on line {line_number}')
  return _validate_preset_build_profiles(profile_ids)

def _validate_preset_build_profiles(profile_ids):
  if profile_ids is None:
    return None
  if not isinstance(profile_ids, list):
    raise ValueError('Build profile assignments must be an array')
  if not profile_ids:
    raise ValueError('Select one or more build profiles, or leave the preset compatible with all profiles')
  normalized = []
  seen = set()
  for profile_id in profile_ids:
    profile_id = str(profile_id or '').strip()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', profile_id):
      raise ValueError(f'Invalid build profile id: {profile_id or "(empty)"}')
    if profile_id in seen:
      raise ValueError(f'Duplicate build profile id: {profile_id}')
    seen.add(profile_id)
    normalized.append(profile_id)
  return normalized

def _preset_supports_build_profile(profile_ids, active_profile_id):
  return profile_ids is None or active_profile_id in profile_ids

def _params_from_mapping(mapping):
  return [{'key': key, 'value': value} for key, value in mapping.items()]

def _preset_model_exists(name, model):
  """Report local model availability without rejecting editable stale presets."""
  if model:
    return os.path.isfile(_resolved_model_path(model))
  return os.path.isfile(os.path.join(MODELS_DIR, os.path.basename(name)))

def _optional_model_file_exists(path):
  """Return None when unused, otherwise report whether an optional companion exists."""
  if not path:
    return None
  return os.path.isfile(_resolved_model_path(path))

def _resolved_model_path(model):
  path = os.path.expanduser(model)
  if not os.path.isabs(path):
    path = os.path.join(APP_ROOT, path)
  return os.path.realpath(path)

def _model_file_kind(filename):
  """Classify conventional llama.cpp companion filenames without parsing GGUF data."""
  basename = os.path.basename(filename).lower()
  if basename.startswith('mmproj'):
    return 'mmproj'
  if basename.startswith('mtp'):
    return 'mtp'
  return 'model'

def _stored_file_message(action, filename):
  kind = _model_file_kind(filename)
  if kind == 'mmproj':
    return f"{action} {filename}. Attach it to a serving preset as its multimodal projector."
  if kind == 'mtp':
    return f"{action} {filename}. Attach it to a serving preset as its MTP draft file."
  return f"{action} {filename}. Configure its serving preset."

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

def _render_model_preset_content(global_params, presets, active_profile_id=None, include_controller_comments=True):
  lines = ['[*]']
  for entry in global_params:
    lines.append(f"{entry['key']} = {entry['value']}")
  for preset in presets:
    if active_profile_id is not None and not _preset_supports_build_profile(
      preset.get('build_profiles'), active_profile_id
    ):
      continue
    lines.append('')
    if include_controller_comments and preset.get('build_profiles') is not None:
      lines.append(
        f"{PRESET_BUILD_PROFILE_COMMENT_PREFIX}{','.join(preset['build_profiles'])}"
      )
    lines.append(f"[{preset['name']}]")
    for entry in preset['params']:
      lines.append(f"{entry['key']} = {entry['value']}")
  return '\n'.join(lines) + '\n'

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
      'build_profiles': _validate_preset_build_profiles(preset.get('build_profiles')),
    })

  content = _render_model_preset_content(global_params, normalized_presets)
  temp_file = MODELS_PRESET_FILE + '.tmp'
  with open(temp_file, 'w') as f:
    f.write(content)
  os.replace(temp_file, MODELS_PRESET_FILE)

def write_active_model_presets(active_profile_id):
  """Render only presets compatible with the profile that is about to run."""
  preset_config = load_model_presets()
  content = _render_model_preset_content(
    preset_config['global_params'],
    preset_config['presets'],
    active_profile_id=active_profile_id,
    include_controller_comments=False,
  )
  temp_file = ACTIVE_MODELS_PRESET_FILE + '.tmp'
  with open(temp_file, 'w') as f:
    f.write(content)
  os.replace(temp_file, ACTIVE_MODELS_PRESET_FILE)

def start_llama():
  global llama_process
  stop_llama()

  profile = _active_build_profile()
  server_bin = _profile_server_bin(profile)

  if not os.path.exists(server_bin):
    return False, f'Active profile "{profile["name"]}" is not built. Please build it first.'

  try:
    write_active_model_presets(profile['id'])
  except (OSError, ValueError) as e:
    return False, f'Could not prepare presets for active profile "{profile["name"]}": {e}'

  server_port = os.environ.get("LLAMA_SERVER_PORT", "8080") if os.environ.get('NATIVE_RUN') else "8080"
  cmd = [
    server_bin,
    "--models-preset", ACTIVE_MODELS_PRESET_FILE,
    "--host", "0.0.0.0",
    "--port", server_port,
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
  llama_server_url = os.environ.get('LLAMA_SERVER_URL', 'http://localhost:8080')
  return render_template('index.html', version=__version__, llama_server_url=llama_server_url)

@app.route('/favicon.ico')
def favicon():
  return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.svg', mimetype='image/svg+xml')

@app.route('/llama-icon.svg')
def llama_icon():
  icon_path = os.path.join(_profile_repo_path(_active_build_profile()), 'build', 'tools', 'ui', 'dist', 'favicon.svg')
  if os.path.exists(icon_path):
    return send_from_directory(os.path.dirname(icon_path), 'favicon.svg', mimetype='image/svg+xml')
  # Fallback: serve favicon if llama.cpp hasn't been cloned yet
  return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.svg', mimetype='image/svg+xml')

@app.route('/api/config', methods=['GET'])
def get_config():
  return jsonify(load_config())

@app.route('/api/llama-help')
def llama_help():
  server_bin = _profile_server_bin(_active_build_profile())
  if not os.path.exists(server_bin):
    return jsonify({"help": "(Active profile is not built yet — click Save & Build first.)"})
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
  if not isinstance(data, dict):
    return jsonify({"success": False, "message": "Configuration must be an object."}), 400
  existing = load_config()
  previous_active = existing['active_build_profile']
  merged = dict(existing)
  merged.update(data)
  try:
    save_config(merged)
    saved = load_config()
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": str(e)}), 400

  if saved['active_build_profile'] != previous_active:
    restarted, restart_message = start_llama()
    if restarted:
      restart_message = 'Active profile saved and llama-server restarted.'
    else:
      restart_message = f'Active profile saved. {restart_message}'
    return jsonify({"success": True, "restarted": restarted, "message": restart_message})
  return jsonify({"success": True, "restarted": False, "message": "Build profiles saved."})

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

  gguf_files = [
    f for f in all_files
    if f.lower().endswith('.gguf')
    and _model_file_kind(f) == 'model'
    and os.path.isfile(os.path.join(MODELS_DIR, f))
  ]

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

@app.route('/api/status')
def get_status():
  profile = _active_build_profile()
  server_bin = _profile_server_bin(profile)
  binary_built = os.path.exists(server_bin)
  server_running = llama_process is not None and llama_process.poll() is None
  return jsonify({
    "binary_built": binary_built,
    "server_running": server_running,
    "active_profile": {"id": profile['id'], "name": profile['name']},
  })

def _normalize_git_url(url):
  if not url:
    return ''
  url = url.strip()
  if url.endswith('.git'):
    url = url[:-4]
  return url.rstrip('/')

def install_build_packages(profile, log_file=None):
  extra_packages = str(profile.get('extra_packages') or '').strip()
  packages = shlex.split(extra_packages)
  if not packages:
    return
  output = log_file or subprocess.DEVNULL
  if log_file:
    log_file.write(f"Installing profile packages: {' '.join(packages)}\n")
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
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
      return jsonify({"success": False, "message": "Build request must be an object."}), 400
    config = load_config()
    profile_id = str(body.get('profile_id') or config['active_build_profile']).strip()
    profile = _get_build_profile(profile_id, config)
    if profile is None:
      return jsonify({"success": False, "message": "Build profile not found."}), 404
    repo_url = profile['repo_url']
    branch = profile['branch']
    repo_path = _profile_repo_path(profile)
    profile_root = os.path.dirname(repo_path)
    os.makedirs(profile_root, exist_ok=True)

    # Never replace a checkout. A profile has a stable repository directory,
    # and a conflicting origin must be resolved by creating a new profile.
    if os.path.exists(repo_path):
      if not os.path.isdir(os.path.join(repo_path, '.git')):
        if os.listdir(repo_path):
          raise ValueError(
            f'Profile checkout {repo_path} exists and is not empty, but is not a git repository; it was left untouched.'
          )
        # Empty directory: proceed to git clone
      else:
        origin_proc = subprocess.run(
          ["git", "remote", "get-url", "origin"],
          cwd=repo_path, capture_output=True, text=True
        )
        current_origin = origin_proc.stdout.strip()
        if origin_proc.returncode != 0:
          raise ValueError(f'Could not read the origin for profile "{profile["name"]}".')
        if _normalize_git_url(current_origin) != _normalize_git_url(repo_url):
          raise ValueError(
            f'Profile "{profile["name"]}" already contains {current_origin}; '
            'create a new profile for a different repository. The checkout was left untouched.'
          )

    with open(BUILD_LOG_FILE, 'w') as build_log:
      build_log.write(
        f'Building profile: {profile["name"]}\n'
        f'Repository: {repo_url}\n'
        f'Branch: {branch}\n\n'
      )
      build_log.flush()
      install_build_packages(profile, build_log)

      if not os.path.isdir(os.path.join(repo_path, '.git')):
        result = subprocess.run(
          ["git", "clone", repo_url, repo_path],
          stdout=build_log, stderr=subprocess.STDOUT, cwd=profile_root
        )
        if result.returncode != 0:
          raise subprocess.CalledProcessError(result.returncode, 'git clone')

      # Fetch the target branch and build only the llama-server target.
      extra_cmake = shlex.split(profile['cmake_params'])
      build_commands = [
        ["git", "fetch", "origin", branch],
        ["git", "checkout", "-B", branch, f"origin/{branch}"],
        ["git", "reset", "--hard", f"origin/{branch}"],
        ["cmake", "-B", "build", "-DCMAKE_BUILD_TYPE=Release", *extra_cmake],
        ["cmake", "--build", "build", "--target", "llama-server", "-j"],
      ]
      for build_cmd in build_commands:
        result = subprocess.run(
          build_cmd, cwd=repo_path,
          stdout=build_log, stderr=subprocess.STDOUT
        )
        if result.returncode != 0:
          raise subprocess.CalledProcessError(result.returncode, build_cmd)

    current_config = load_config()
    if profile_id == current_config['active_build_profile']:
      restarted, message = start_llama()
      if restarted:
        message = f'Profile "{profile["name"]}" built and llama-server restarted.'
      else:
        message = f'Profile "{profile["name"]}" built. {message}'
      return jsonify({"success": True, "restarted": restarted, "message": message})
    return jsonify({
      "success": True,
      "restarted": False,
      "message": f'Profile "{profile["name"]}" built. The active server was not changed.',
    })
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
      models.append({
        "name": f,
        "size": os.path.getsize(path),
        "kind": _model_file_kind(f),
      })
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
    "kind": _model_file_kind(safe_name),
    "message": _stored_file_message("Uploaded", safe_name),
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
    updated_presets = []
    for preset in preset_config['presets']:
      model = next((param['value'] for param in preset['params'] if param['key'] == 'model'), '')
      implicit_model = not model and os.path.basename(preset['name']) == os.path.basename(filename)
      if (model and _resolved_model_path(model) == target_path) or implicit_model:
        removed_presets.append(preset['name'])
      else:
        params = [
          param for param in preset['params']
          if not (
            param['key'] in COMPANION_PARAM_KEYS
            and param['value']
            and _resolved_model_path(param['value']) == target_path
          )
        ]
        if len(params) != len(preset['params']):
          updated_presets.append(preset['name'])
        remaining_presets.append({
          'name': preset['name'],
          'params': params,
          'build_profiles': preset.get('build_profiles'),
        })
    # Validate before the model file is removed so a malformed hand-edited INI
    # cannot leave a model deleted without its matching presets updated.
    global_params = [
      param for param in preset_config['global_params']
      if not (
        param['key'] in COMPANION_PARAM_KEYS
        and param['value']
        and _resolved_model_path(param['value']) == target_path
      )
    ]
    global_params_updated = len(global_params) != len(preset_config['global_params'])
    _validate_params(global_params)
    for preset in remaining_presets:
      _validate_params(preset['params'])
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Could not update models.ini: {e}"}), 400

  os.remove(safe_path)
  presets_changed = bool(removed_presets or updated_presets or global_params_updated)
  if not presets_changed:
    return jsonify({
      "success": True,
      "removed_presets": [],
      "updated_presets": [],
      "message": f"Deleted {filename}.",
    })

  try:
    save_model_presets(global_params, remaining_presets)
  except (OSError, ValueError) as e:
    return jsonify({"success": False, "message": f"Deleted {filename}, but could not save models.ini: {e}"}), 500
  restarted, message = start_llama()
  restart_message = "llama-server restarted." if restarted else message
  changes = []
  if removed_presets:
    changes.append(f"removed {len(removed_presets)} serving preset(s)")
  companion_reference_count = len(updated_presets) + (1 if global_params_updated else 0)
  if companion_reference_count:
    changes.append(f"removed {companion_reference_count} companion reference(s)")
  return jsonify({
    "success": True,
    "removed_presets": removed_presets,
    "updated_presets": updated_presets,
    "restarted": restarted,
    "message": f"Deleted {filename} and {' and '.join(changes)}. {restart_message}",
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
      yield f"data: {json.dumps({'success': True, 'filename': safe_name, 'kind': _model_file_kind(safe_name), 'message': _stored_file_message('Downloaded', safe_name)})}\n\n"
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

@app.route('/api/logs/download')
def download_logs():
  if not os.path.exists(LOG_FILE):
    resp = Response("", mimetype='text/plain', headers={
      'Content-Disposition': 'attachment; filename="llama-server.log"'
    })
  else:
    resp = send_file(
      LOG_FILE,
      mimetype='text/plain',
      as_attachment=True,
      download_name='llama-server.log'
    )
  resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
  resp.headers['Pragma'] = 'no-cache'
  resp.headers['Expires'] = '0'
  return resp

@app.route('/api/build-logs/download')
def download_build_logs():
  if not os.path.exists(BUILD_LOG_FILE):
    resp = Response("", mimetype='text/plain', headers={
      'Content-Disposition': 'attachment; filename="llama-build.log"'
    })
  else:
    resp = send_file(
      BUILD_LOG_FILE,
      mimetype='text/plain',
      as_attachment=True,
      download_name='llama-build.log'
    )
  resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
  resp.headers['Pragma'] = 'no-cache'
  resp.headers['Expires'] = '0'
  return resp

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
  if os.environ.get('NATIVE_RUN'):
    port = int(os.environ.get('LLAMA_CONTROLLER_PORT', 5000))
  else:
    port = 5000
  app.run(host='0.0.0.0', port=port)

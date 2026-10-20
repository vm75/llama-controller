"""Run with: python -m unittest discover -s tests."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('controller', Path(__file__).resolve().parents[1] / 'server/server.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class HuggingFaceTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.models = Path(self.temp.name) / 'models'
    self.models.mkdir()
    self.client = controller.app.test_client()
    self.paths = patch.multiple(controller, MODELS_DIR=str(self.models), MODELS_PRESET_FILE=str(Path(self.temp.name) / 'models.ini'))
    self.paths.start()
    self.addCleanup(self.paths.stop)

  def test_explicit_url_overrides_default_quant(self):
    tree = [{'path': name, 'size': 4} for name in ['model.Q4_K_M.gguf', 'model.Q8_0.gguf']]
    with patch.object(controller, 'fetch_hf_tree', return_value=tree):
      response = self.client.post('/api/hf/resolve', json={'source': 'https://huggingface.co/owner/repo/blob/main/model.Q8_0.gguf'})
    groups = response.get_json()['model_groups']
    self.assertEqual([g['id'] for g in groups], ['model.Q8_0.gguf'])
    self.assertTrue(groups[0]['selected'])

  def test_external_symlink_does_not_break_listing_or_refresh(self):
    external = Path(self.temp.name) / 'external.gguf'
    external.write_bytes(b'gguf')
    (self.models / 'linked.gguf').symlink_to(external)
    self.assertEqual(self.client.get('/api/models').get_json()['models'], [])
    with patch.object(controller, 'load_model_presets', return_value={'presets': [], 'global_params': []}):
      response = self.client.post('/api/model-presets/refresh')
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.get_json()['added_count'], 0)

  def test_partial_shard_selection_rejected(self):
    tree = [{'path': f'model-0000{i}-of-00002.gguf', 'size': 4} for i in (1, 2)]
    with patch.object(controller, 'fetch_hf_tree', return_value=tree):
      response = self.client.post('/api/download', json={'repo': 'owner/repo', 'files': [tree[0]]})
    self.assertEqual(response.status_code, 400)
    self.assertIn('every shard', response.get_json()['message'])

  def test_incomplete_repository_group_not_selected(self):
    tree = [{'path': 'model-00001-of-00002.gguf', 'size': 4}]
    with patch.object(controller, 'fetch_hf_tree', return_value=tree):
      response = self.client.post('/api/hf/resolve', json={'source': 'https://huggingface.co/owner/repo/blob/main/' + tree[0]['path']})
    group = response.get_json()['model_groups'][0]
    self.assertFalse(group['complete'])
    self.assertFalse(group['selected'])

  def test_complete_shards_and_companion_download_together(self):
    tree = [{'path': name, 'size': 4} for name in ['model-00001-of-00002.gguf', 'model-00002-of-00002.gguf', 'mmproj-model.gguf']]
    with patch.object(controller, 'fetch_hf_tree', return_value=tree), patch.object(controller.urllib.request, 'urlopen', side_effect=lambda *args, **kwargs: io.BytesIO(b'gguf')):
      response = self.client.post('/api/download', json={'repo': 'owner/repo', 'files': tree})
      self.assertIn('"success": true', response.get_data(as_text=True))
    for item in tree:
      self.assertEqual((self.models / 'owner/repo' / item['path']).read_bytes(), b'gguf')

  def test_equal_size_other_revision_is_replaced(self):
    target = self.models / 'owner/repo/model.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old!')
    tree = [{'path': 'model.gguf', 'size': 4}]
    with patch.object(controller, 'fetch_hf_tree', return_value=tree), patch.object(controller.urllib.request, 'urlopen', return_value=io.BytesIO(b'new!')) as opened:
      response = self.client.post('/api/download', json={'repo': 'owner/repo', 'revision': 'other', 'files': tree})
      self.assertIn('"success": true', response.get_data(as_text=True))
    self.assertEqual(target.read_bytes(), b'new!')
    self.assertIn('/resolve/other/', opened.call_args.args[0])
    self.assertFalse(Path(str(target) + '.part').exists())

  def test_failed_transfer_preserves_previous_weights(self):
    target = self.models / 'owner/repo/model.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'old!')
    with patch.object(controller, 'fetch_hf_tree', return_value=[{'path': 'model.gguf', 'size': 4}]), patch.object(controller.urllib.request, 'urlopen', return_value=io.BytesIO(b'x')):
      response = self.client.post('/api/download', json={'repo': 'owner/repo', 'filename': 'model.gguf'})
      self.assertIn('"event": "error"', response.get_data(as_text=True))
    self.assertEqual(target.read_bytes(), b'old!')
    self.assertFalse(Path(str(target) + '.part').exists())


if __name__ == '__main__':
  unittest.main()

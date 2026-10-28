"""Run stdlib tests in a disposable profile with no inherited auth/environment."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent
host = Path(os.environ.get('HERMES_SOURCE', str(Path.home() / '.hermes/hermes-agent')))
scratch = root.parent / '.prompt-models-test-tmp'
scratch.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='profile-', dir=scratch) as directory:
    home = Path(directory)
    plugins = home / 'plugins'
    plugins.mkdir()
    if '--doctor' not in sys.argv:
        (plugins / 'prompt-models').symlink_to(root, target_is_directory=True)
    (home / 'config.yaml').write_text('plugins:\n  enabled: [prompt-models]\nmodel:\n  provider: openai-codex\n  default: gpt-test\n', encoding='utf-8')
    env = {'HOME': directory, 'HERMES_HOME': directory, 'PATH': os.environ.get('PATH', ''),
           'PYTHONPATH': os.pathsep.join([str(root), str(host)]), 'PYTHONNOUSERSITE': '1',
           'TMPDIR': directory, 'HERMES_TEST_MODE': '1', 'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    command = ([sys.executable, '-m', 'hermes_cli.main', 'plugins', 'doctor', str(root), '--ci']
               if '--doctor' in sys.argv else [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'])
    result = subprocess.run(command, cwd=root, env=env)
    sys.exit(result.returncode)

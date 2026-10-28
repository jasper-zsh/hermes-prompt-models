"""Run real React DOM tests using the host's existing Node dependencies, then remove the link."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parent
host = Path(os.environ.get('HERMES_SOURCE', str(Path.home() / '.hermes/hermes-agent')))
link = root / 'node_modules'
created = not link.exists()
if created:
    link.symlink_to(host / 'node_modules', target_is_directory=True)
try:
    result = subprocess.run([str(link / '.bin/vitest'), 'run', '--config', 'vitest.config.mjs'], cwd=root)
finally:
    if created and link.is_symlink():
        link.unlink()
sys.exit(result.returncode)

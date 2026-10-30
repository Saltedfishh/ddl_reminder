"""Hidden entry point for the optional per-user startup shortcut."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(root / 'data' / 'browsers')
(root / 'data').mkdir(exist_ok=True)
sys.stdout = sys.stderr = open(root / 'data' / 'background.log', 'a', encoding='utf-8')
sys.argv = [str(root / 'app.py'), '--no-browser']
from app import main

try:
    main()
except OSError as exc:
    print(f'Background could not start (another instance may be running): {exc}', flush=True)

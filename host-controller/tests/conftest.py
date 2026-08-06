"""Put the host-controller package root on sys.path so ``import vm_controller`` works when pytest
is invoked from anywhere in the repo."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

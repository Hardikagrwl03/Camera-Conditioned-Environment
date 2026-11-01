import sys
from pathlib import Path

import pytest
import torch

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts", ROOT / "scripts" / "windows", Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

torch.set_num_threads(4)


@pytest.fixture
def tmp(tmp_path):
    return tmp_path

from __future__ import annotations
import json
from pathlib import Path

def load_settings(root="."):
    root=Path(root).resolve(); p=root/"config"/"settings.json"
    if not p.exists(): p=root/"config"/"settings.example.json"
    return json.loads(p.read_text(encoding="utf-8"))

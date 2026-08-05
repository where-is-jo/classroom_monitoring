"""Jinja2 템플릿 설정.

템플릿 디렉터리는 `app/` 밖에 둔다. Python 코드와 템플릿 파일을 섞지 않기 위해서다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

# app/shared/templating.py -> webapps/fastapi/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

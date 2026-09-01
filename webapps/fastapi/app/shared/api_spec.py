"""구현 전 API 설계 명세를 Swagger UI로 제공한다.

명세 문서(`api-spec/openapi.json`)는 `individual_tasks/API명세서.md`를 OpenAPI로 옮긴 것이고,
아직 구현되지 않은 계약이다. 그래서 애플리케이션이 자동 생성하는 `/openapi.json`과 **섞지 않고**
별도 경로로 낸다. 실제로 동작하는 API 문서는 `/docs`가 정본이다.

명세를 코드가 아니라 문서 파일로 두는 이유는, 구현하지 않은 엔드포인트를 라우터로 만들면
`/docs`에 "있는 기능"처럼 보이기 때문이다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

# app/shared/api_spec.py -> webapps/fastapi/
SPEC_PATH = Path(__file__).resolve().parent.parent.parent / "api-spec" / "openapi.json"

SPEC_JSON_URL = "/api-spec.json"
SPEC_DOCS_URL = "/docs/api-spec"

# 애플리케이션의 실제 API가 아니므로 앱의 OpenAPI 스키마에 넣지 않는다.
router = APIRouter(include_in_schema=False)


@lru_cache(maxsize=1)
def load_design_spec() -> dict[str, Any]:
    """설계 명세 문서를 읽는다. 요청마다 다시 읽을 이유가 없어 한 번만 읽는다."""
    document: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return document


@router.get(SPEC_JSON_URL)
def design_spec_document() -> JSONResponse:
    return JSONResponse(content=load_design_spec())


@router.get(SPEC_DOCS_URL)
def design_spec_docs() -> HTMLResponse:
    """구현 전 명세이므로 요청 실행(Try it out)을 끈다.

    켜 두면 존재하지 않는 경로로 요청이 나가 404를 받고, 명세가 틀린 것처럼 읽힌다.
    """
    return get_swagger_ui_html(
        openapi_url=SPEC_JSON_URL,
        title="Smart Office Monitoring — API 설계 명세",
        swagger_ui_parameters={
            "supportedSubmitMethods": [],
            "tryItOutEnabled": False,
            "docExpansion": "list",
        },
    )

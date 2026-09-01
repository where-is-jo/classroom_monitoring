"""`app/shared/database.py`의 순수 헬퍼 검증."""

from __future__ import annotations

import pytest
from bson import ObjectId

from app.shared.database import document_id, document_id_filter

# ============================================================
# 앱 밖에서 들어온 문서의 _id (ObjectId)
# ============================================================


def test_문자열_id를_그대로_읽는다() -> None:
    assert document_id({"_id": "student-1"}) == "student-1"


def test_ObjectId_id도_문자열로_읽는다() -> None:
    """Compass·적재 스크립트가 넣은 문서는 _id가 ObjectId다.

    문자열만 받아들이면 그 문서 하나 때문에 목록 조회가 통째로 실패한다.
    실제로 학생 3건이 ObjectId여서 학생·좌석·ROI 화면이 모두 500이 났다.
    """
    oid = ObjectId()

    assert document_id({"_id": oid}) == str(oid)


def test_id가_문자열도_ObjectId도_아니면_거부한다() -> None:
    with pytest.raises(TypeError):
        document_id({"_id": 12345})


def test_빈_문자열_id는_거부한다() -> None:
    with pytest.raises(ValueError):
        document_id({"_id": ""})


def test_ObjectId_문자열로_조회하면_두_형식을_모두_맞춘다() -> None:
    """`document_id`가 돌려준 문자열로 되조회할 수 있어야 한다.

    맞추지 않으면 목록에는 보이는데 상세·수정만 조용히 실패한다.
    """
    oid = ObjectId()

    result = document_id_filter(str(oid))

    assert result == {"_id": {"$in": [str(oid), oid]}}


def test_일반_문자열_id는_그대로_조회한다() -> None:
    """앱이 만든 UUID는 ObjectId가 아니므로 $in으로 감쌀 이유가 없다."""
    assert document_id_filter("student-1") == {"_id": "student-1"}

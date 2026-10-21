from app.llm import preset_guidance, reorder_table_first


def test_preset_guidance_empty():
    assert preset_guidance(None) is None
    assert preset_guidance({"labels": []}) is None


def test_preset_guidance_uses_labels():
    hint = preset_guidance({"labels": ["Подача", "Напор"]})
    assert hint
    assert "Подача" in hint
    assert "Напор" in hint
    assert "не ограничение" in hint


def test_reorder_table_first_moves_target_to_front():
    frags = [{"id": "p1-t1"}, {"id": "p2-t1"}, {"id": "p2-t2"}]
    out = reorder_table_first(frags, "p2-t1")
    assert [f["id"] for f in out] == ["p2-t1", "p1-t1", "p2-t2"]
    # исходный список не меняется
    assert [f["id"] for f in frags] == ["p1-t1", "p2-t1", "p2-t2"]


def test_reorder_table_first_noop_for_unknown_or_none():
    frags = [{"id": "a"}, {"id": "b"}]
    assert [f["id"] for f in reorder_table_first(frags, "zzz")] == ["a", "b"]
    assert [f["id"] for f in reorder_table_first(frags, None)] == ["a", "b"]

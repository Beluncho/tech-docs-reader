"""Тесты промптов LLM-ассистента маппинга (этап 4.1).

Проверяют контракт файлов в backend/app/prompts/: наличие, плейсхолдеры,
разбираемость JSON-схемы в system.txt и эталонных ответов в examples/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "backend" / "app" / "prompts"

ROLES = {"model", "param", "value", "unit", "section", "condition", "ignore"}

USER_TEMPLATES = ["matrix_table.txt", "keyvalue_table.txt", "complex_table.txt"]

PLACEHOLDERS = [
    "{doc_context}",
    "{page}",
    "{table_meta}",
    "{table_markdown}",
    "{merged_cells}",
    "{context_hint}",
]

EXAMPLE_FILES = [
    "cns_pump.txt",
    "welch_pump.txt",
    "cm_pump.txt",
    "oprosny_list.txt",
]


def _read(name: str) -> str:
    path = PROMPTS_DIR / name
    assert path.is_file(), f"нет файла промпта: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["system.txt", "README.md", *USER_TEMPLATES])
def test_prompt_files_exist_and_nonempty(name: str) -> None:
    text = _read(name)
    assert text.strip(), f"{name} пуст"
    # UTF-8 без BOM
    assert not PROMPTS_DIR.joinpath(name).read_bytes().startswith(b"\xef\xbb\xbf")


def test_system_mentions_all_roles() -> None:
    text = _read("system.txt")
    for role in ROLES:
        assert role in text, f"роль {role} не описана в system.txt"


def test_system_json_schema_is_valid() -> None:
    """JSON-схема ответа в system.txt должна разбираться и содержать ключевые поля."""
    text = _read("system.txt")
    match = re.search(r"\{[^{}]*\"orientation\".*?\n\}", text, re.DOTALL)
    assert match, "в system.txt нет JSON-схемы ответа"
    schema = json.loads(match.group(0))
    assert schema["annotations"][0]["role"] == "model"
    assert "range" in schema["annotations"][0]
    assert "notes" in schema


@pytest.mark.parametrize("name", USER_TEMPLATES)
def test_user_templates_have_all_placeholders(name: str) -> None:
    text = _read(name)
    for ph in PLACEHOLDERS:
        assert ph in text, f"{name}: нет плейсхолдера {ph}"
    # после подстановки не должно остаться фигурных плейсхолдеров
    rendered = text
    for ph in PLACEHOLDERS:
        rendered = rendered.replace(ph, "X")
    assert not re.search(r"\{[a-z_]+\}", rendered), f"{name}: неизвестный плейсхолдер"


def _split_example(name: str) -> tuple[str, str]:
    text = _read(f"examples/{name}")
    assert "=== USER ===" in text and "=== ASSISTANT ===" in text, f"{name}: нет секций"
    _, rest = text.split("=== USER ===", 1)
    user, assistant = rest.split("=== ASSISTANT ===", 1)
    return user.strip(), assistant.strip()


@pytest.mark.parametrize("name", EXAMPLE_FILES)
def test_examples_answer_is_valid_json(name: str) -> None:
    _, assistant = _split_example(name)
    assert not assistant.startswith("```"), f"{name}: эталон не должен быть в markdown-блоке"
    data = json.loads(assistant)
    assert data["orientation"] in {"rows", "columns", "none"}
    assert data["structure"] in {"matrix", "keyvalue", "list", "other"}
    assert data["complexity"] in {"low", "medium", "high"}
    assert data["annotations"], f"{name}: пустой список аннотаций"


@pytest.mark.parametrize("name", EXAMPLE_FILES)
def test_examples_annotations_follow_contract(name: str) -> None:
    _, assistant = _split_example(name)
    data = json.loads(assistant)
    for ann in data["annotations"]:
        assert ann["role"] in ROLES, f"{name}: неизвестная роль {ann['role']}"
        has_cell = "row" in ann and "col" in ann
        has_range = "range" in ann
        assert has_cell or has_range, f"{name}: аннотация без row/col и без range"
        assert not (has_cell and has_range), f"{name}: row/col и range одновременно"
        if has_cell:
            assert isinstance(ann["row"], int) and ann["row"] >= 0
            assert isinstance(ann["col"], int) and ann["col"] >= 0
        else:
            r = ann["range"]
            assert isinstance(r, list) and len(r) == 4
            assert all(isinstance(x, int) and x >= 0 for x in r)
            assert r[0] <= r[2] and r[1] <= r[3], f"{name}: вырожденный range {r}"
        conf = ann.get("confidence", 1.0)
        assert 0.0 <= conf <= 1.0, f"{name}: confidence вне [0,1]"
        for alt in ann.get("alternatives", []):
            assert alt["role"] in ROLES
            assert 0.0 <= alt.get("confidence", 0.0) <= 1.0


@pytest.mark.parametrize("name", EXAMPLE_FILES)
def test_examples_user_part_has_table(name: str) -> None:
    user, _ = _split_example(name)
    assert user.startswith("Документ:") and "Страница:" in user and "Таблица:" in user
    assert "| # |" in user, f"{name}: нет колонки с номерами строк"
    assert "Merged cells:" in user


def test_matrix_examples_cover_both_orientations() -> None:
    orientations = {}
    for name in EXAMPLE_FILES:
        _, assistant = _split_example(name)
        data = json.loads(assistant)
        orientations[data["structure"]] = orientations.get(data["structure"], 0) + 1
    assert orientations.get("matrix", 0) >= 2
    assert orientations.get("keyvalue", 0) >= 1
    # обе ориентации матриц покрыты
    orients = set()
    for name in ("cns_pump.txt", "welch_pump.txt", "cm_pump.txt"):
        _, assistant = _split_example(name)
        orients.add(json.loads(assistant)["orientation"])
    assert orients == {"rows", "columns"}

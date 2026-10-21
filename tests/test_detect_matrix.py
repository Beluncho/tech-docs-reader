"""Тесты эвристик матричной структуры модель×параметр (подэтап 2.4)."""
from app.detect import annotate_matrix, detect_matrix_structure, detect_tables_from_grid, merge_page_tables
from app.preset import Cell, Table


def _mk_table(text_rows, bbox=(0.0, 0.0, 100.0, 100.0), source="word", has_merged=False):
    rows = [
        [Cell(text=t, row=i, col=j, page=0) for j, t in enumerate(r)]
        for i, r in enumerate(text_rows)
    ]
    return Table(id="x", rows=rows, bbox=list(bbox), source=source, has_merged_cells=has_merged)


# --- ориентация «rows»: модели в первой колонке -----------------------------

ROWS_MATRIX = [
    ["Обозначение", "Подача, м3/ч", "Напор, м", "Мощность, кВт"],
    ["НС 13-70 НСГ 13-70", "13", "70", "11"],
    ["НС 13-105 НСГ 13-105", "13", "105", "11"],
    ["НС 13-140 НСГ 13-140", "13", "140", "15"],
    ["НСМ 13-175 НСн 13-175", "13", "175", "18,5"],
]


def test_matrix_rows_orientation():
    t = _mk_table(ROWS_MATRIX)
    info = detect_matrix_structure(t)
    assert info == {"is_matrix": True, "orientation": "rows"}


def test_matrix_rows_requires_header_params():
    # нет шапки с >=2 ячейками-параметрами и не merged — не матрица
    rows = [["x", "y", "z"]] + [r[1:] for r in ROWS_MATRIX[1:2]] + [
        ["НС 13-70", "13", "70"], ["НС 13-105", "13", "105"], ["НС 13-140", "13", "140"]
    ]
    # пересобираем с моделями в col0
    rows = [["a", "b", "c"],
            ["НС 13-70", "13", "70"],
            ["НС 13-105", "13", "105"],
            ["НС 13-140", "13", "140"]]
    t = _mk_table(rows)
    assert detect_matrix_structure(t)["is_matrix"] is False


def test_matrix_rows_merged_without_header():
    # merged-таблица (НС): шапка не обязана быть распознана, merged сам признак
    rows = [["", "a", "b"], ["НС 13-70", "13", "70"],
            ["НС 13-105", "13", "105"], ["НС 13-140", "13", "140"]]
    t = _mk_table(rows, has_merged=True)
    assert detect_matrix_structure(t) == {"is_matrix": True, "orientation": "rows"}


def test_matrix_rows_min_thresholds():
    # 2 строки моделей — мало
    rows = [["Тип", "Подача, м3/ч", "Напор, м"],
            ["СМ80-50-200-2", "30", "48"],
            ["СМ100-65-250-2", "100", "100"]]
    assert detect_matrix_structure(_mk_table(rows))["is_matrix"] is False

    # 1 колонка значений — мало
    rows = [["Тип", "Подача, м3/ч"],
            ["СМ80-50-200-2", "30"],
            ["СМ100-65-250-2", "100"],
            ["СМ150-125-315-2", "200"],
            ["СМ200-150-400-2", "500"]]
    assert detect_matrix_structure(_mk_table(rows))["is_matrix"] is False


# --- ориентация «columns»: модели в шапке -----------------------------------

COLUMNS_MATRIX = [
    ["Parameter", "Unit", "2522", "2534", "2546"],
    ["Pumping speed", "m3/h", "1.1", "1.7", "2.3"],
    ["Ultimate pressure", "mbar", "133", "93", "80"],
    ["Overpressure", "bar", "7.9", "5.8", "5.2"],
    ["Motor power", "kW", "1.1", "1.5", "2.2"],
]


def test_matrix_columns_orientation():
    t = _mk_table(COLUMNS_MATRIX)
    info = detect_matrix_structure(t)
    assert info == {"is_matrix": True, "orientation": "columns"}


def test_matrix_columns_with_model_codes():
    # дефисные коды моделей в шапке (1Кс-стиль)
    rows = [
        ["Показатель", "1Кс12-50-...", "1Кс20-50", "1Кс32-50"],
        ["Подача, м3/ч", "12", "20", "32"],
        ["Напор, м", "50", "50", "50"],
        ["Мощность, кВт", "5,5", "5,5", "7,5"],
    ]
    assert detect_matrix_structure(_mk_table(rows)) == {"is_matrix": True, "orientation": "columns"}


def test_matrix_columns_numbering_not_models():
    # строка нумерации «1 2 3 4» — не модели
    rows = [
        ["", "1", "2", "3", "4"],
        ["Подача", "12", "20", "32", "50"],
        ["Напор", "50", "50", "50", "50"],
        ["Мощность", "5,5", "5,5", "7,5", "11"],
    ]
    assert detect_matrix_structure(_mk_table(rows))["is_matrix"] is False


def test_matrix_columns_needs_param_rows():
    # шапка с кодами есть, но ниже нет строк «параметр + значения»
    rows = [
        ["Parameter", "Unit", "2522", "2534", "2546"],
        ["Pumping speed", "m3/h", "1.1", "1.7", "2.3"],
    ]
    assert detect_matrix_structure(_mk_table(rows))["is_matrix"] is False


# --- guard-ы -----------------------------------------------------------------

def test_matrix_parts_list_guard():
    # перечень деталей: модели-подобные коды в col0, учётные колонки — не матрица
    rows = [
        ["Наименование", "Кол. шт.", "Масса 1шт. кг", "Примечание"],
        ["Кольцо 220-230-30", "1", "0,015", "СМ80-50-200"],
        ["Кольцо 270-280-30", "1", "0,023", "СМ100-65-250"],
        ["Втулка 340-350-30", "1", "0,028", "СМ150-125-315"],
    ]
    assert detect_matrix_structure(_mk_table(rows))["is_matrix"] is False


def test_matrix_small_table_not_matrix():
    assert detect_matrix_structure(_mk_table([["a", "b", "c"]]))["is_matrix"] is False
    assert detect_matrix_structure(_mk_table([["a"] * 2] * 2))["is_matrix"] is False


# --- аннотация и интеграция ---------------------------------------------------

def test_annotate_matrix_sets_fields():
    t = _mk_table(ROWS_MATRIX)
    assert t.is_matrix is False and t.orientation is None
    annotate_matrix(t)
    assert t.is_matrix is True and t.orientation == "rows"

    t2 = _mk_table([["a", "b", "c"]])
    annotate_matrix(t2)
    assert t2.is_matrix is False and t2.orientation is None


def test_merge_page_tables_annotates_result():
    # word-таблица-матрица проходит merge и получает метки
    matrix = _mk_table(ROWS_MATRIX, bbox=(100.0, 100.0, 400.0, 300.0))
    out = merge_page_tables(0, [], [matrix])
    assert len(out) == 1
    assert out[0].is_matrix is True and out[0].orientation == "rows"


def test_grid_matrix_full_pipeline():
    # синтетическая сетка (как test_detect_grid) с моделями в col0 -> rows-матрица
    from tests.test_detect_grid import _grid_rects, _word

    words = [
        _word(110, 120, "Модель"), _word(210, 120, "Подача, м3/ч"), _word(310, 120, "Напор, м"),
        _word(110, 220, "СМ80-50-200-2"), _word(210, 220, "30"), _word(310, 220, "48"),
        _word(110, 320, "СМ100-65-250-2"), _word(210, 320, "100"), _word(310, 320, "100"),
        _word(110, 420, "СМ150-125-315-2"), _word(210, 420, "200"), _word(310, 420, "140"),
    ]
    tables = detect_tables_from_grid(words, _grid_rects(), 0)
    assert len(tables) == 1
    # без word-таблиц grid-таблица не проходит merge-фильтр (нет merged cells,
    # не transposed); матричность оцениваем напрямую
    assert detect_matrix_structure(tables[0]) == {"is_matrix": True, "orientation": "rows"}

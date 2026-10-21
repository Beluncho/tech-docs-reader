"""Тесты автоопределения строк заголовка (подэтап 2.5)."""
from app.detect import annotate_table, detect_header_rows, merge_page_tables
from app.preset import Cell, Table


def _mk_table(text_rows, bbox=(0.0, 0.0, 100.0, 100.0), source="word", has_merged=False):
    rows = [
        [Cell(text=t, row=i, col=j, page=0) for j, t in enumerate(r)]
        for i, r in enumerate(text_rows)
    ]
    return Table(id="x", rows=rows, bbox=list(bbox), source=source, has_merged_cells=has_merged)


# --- типовые шапки (синтетические) -----------------------------------------------------

def test_header_cns_single_row():
    # НС p6: 1 строка параметров над модельными строками
    rows = [
        ["Обозначение", "Подача, м3/с", "Напор, м", "Мощность, кВт"],
        ["НС 13-70 НСГ 13-70", "13", "70", "11"],
        ["НС 13-105 НСГ 13-105", "13", "105", "11"],
        ["НС 13-140 НСГ 13-140", "13", "140", "15"],
    ]
    t = _mk_table(rows, has_merged=True)
    assert detect_header_rows(t) == 1


def test_header_sm_two_rows_merged():
    # СМ p9-t2: row0 merged «насос (агрегата)», row1 коды колонок — шапка из 2 строк
    rows = [
        ["", "Тип электродвигателя", "", ""],
        ["Типоразмер", "Подача, м3/с", "Напор, м", "Мощность, кВт"],
        ["СМ80-50-200-2", "30", "48", "4"],
        ["СМ100-65-250-2", "100", "100", "11"],
        ["СМ150-125-315-2", "200", "140", "30"],
    ]
    t = _mk_table(rows, has_merged=True)
    assert detect_header_rows(t) == 2


def test_header_welch_columns_orientation():
    # VP p11: модели в шапке, ниже строки «параметр + значения»
    rows = [
        ["Parameter", "Unit", "2522", "2534", "2546"],
        ["Pumping speed", "m3/h", "1.1", "1.7", "2.3"],
        ["Ultimate pressure", "mbar", "133", "93", "80"],
        ["Motor power", "kW", "1.1", "1.5", "2.2"],
    ]
    assert detect_header_rows(_mk_table(rows)) == 1


def test_header_keyvalue_no_header():
    # keyvalue без шапки: первая строка — данные
    rows = [
        ["Изготовитель", "Ливнынасос"],
        ["Гарантийный срок, мес", "24"],
        ["Масса, кг", "115"],
    ]
    assert detect_header_rows(_mk_table(rows)) == 0


def test_header_multiline_params():
    # многострочная шапка: 2 строки текста-параметров, ниже числа
    rows = [
        ["Подача", "Напор", "Мощность"],
        ["Q, м3/с", "H, м", "N, кВт"],
        ["13", "70", "11"],
        ["13", "105", "15"],
        ["13", "140", "18,5"],
    ]
    assert detect_header_rows(_mk_table(rows)) == 2


def test_header_caption_not_included():
    # подпись «Таблица 1 — ...» над шапкой не входит в header_rows
    rows = [
        ["Таблица 1- Основные параметры"],
        ["", "", "", ""],
        ["Обозначение", "Подача, м3/с", "Напор, м", "Мощность, кВт"],
        ["НС 13-70", "13", "70", "11"],
        ["НС 13-105", "13", "105", "11"],
        ["НС 13-140", "13", "140", "15"],
    ]
    assert detect_header_rows(_mk_table(rows)) == 1


def test_header_no_table_header_if_first_row_is_data():
    # первая содержательная строка — данные (модель + значения): шапки нет
    rows = [
        ["СМ80-50-200-2", "30", "48"],
        ["СМ100-65-250-2", "100", "100"],
        ["СМ150-125-315-2", "200", "140"],
    ]
    assert detect_header_rows(_mk_table(rows)) == 0


def test_header_empty_table():
    assert detect_header_rows(_mk_table([])) == 0
    assert detect_header_rows(_mk_table([[], []])) == 0


# --- аннотация и данные ----------------------------------------------------------

def test_annotate_table_sets_header_rows():
    rows = [
        ["Обозначение", "Подача, м3/с", "Напор, м"],
        ["НС 13-70", "13", "70"],
        ["НС 13-105", "13", "105"],
    ]
    t = _mk_table(rows, has_merged=True)
    annotate_table(t)
    assert t.header_rows == 1
    assert t.has_header


def test_iter_data_rows_skips_header():
    rows = [
        ["Типоразмер", "Подача, м3/с", "Напор, м", "Мощность, кВт"],
        ["", "Тип электродвигателя", "", ""],
        ["СМ80-50-200-2", "30", "48", "4"],
        ["СМ100-65-250-2", "100", "100", "11"],
    ]
    t = _mk_table(rows, has_merged=True)
    annotate_table(t)
    data = list(t.iter_data_rows())
    assert len(data) == 2
    assert data[0][0].text == "СМ80-50-200-2"


def test_merge_page_tables_sets_header_rows():
    # интеграция: merge_page_tables проставляет header_rows у итоговой таблицы
    rows = [
        ["Обозначение", "Подача, м3/с", "Напор, м", "Мощность, кВт"],
        ["НС 13-70 НСГ 13-70", "13", "70", "11"],
        ["НС 13-105 НСГ 13-105", "13", "105", "11"],
        ["НС 13-140 НСГ 13-140", "13", "140", "15"],
    ]
    t = _mk_table(rows, bbox=(100.0, 100.0, 400.0, 300.0), has_merged=True)
    out = merge_page_tables(0, [], [t])
    assert len(out) == 1
    assert out[0].header_rows == 1

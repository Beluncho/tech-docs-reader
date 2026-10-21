from app.normalize import normalize_header, normalize_text


def test_normalize_text_lower_and_spaces():
    assert normalize_text("  Подача\u00a0М3 ") == "подача м3"


def test_normalize_header_strips_edges():
    assert normalize_header("№ п/п") == "п/п"
    assert normalize_header("  Ед. изм. ") == "ед. изм"
    assert normalize_header("Наименование продукции") == "наименование продукции"
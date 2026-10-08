from solar_outreach import sheets


def test_col_letters():
    assert [sheets.col(n) for n in (1, 26, 27, 52, 53)] == ["A", "Z", "AA", "AZ", "BA"]


def test_status_formula_points_to_status_column():
    reqs = sheets.format_requests(7)
    c = sheets.col(sheets.TABS["Станции"].index("статус переписки") + 1)
    formulas = [r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"] for r in reqs]
    assert formulas[0] == f'=${c}2="квалифицирована"' and len(reqs) == len(sheets.STATUS_COLORS)


def test_station_columns_cover_all_spec_items():
    head = sheets.TABS["Станции"]
    assert len(head) == len(set(head))
    for item in sheets.ITEMS:
        assert item in head

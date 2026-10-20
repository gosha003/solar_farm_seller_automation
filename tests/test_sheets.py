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


def test_phones_written_as_text():
    """USER_ENTERED parses "+359..." as a number (drops the +, garbles lists): phones must go out as text."""
    class Api:
        def values(self): return self
        def execute(self, **kw): return {}
        def append(self, **kw): self.sent = kw["body"]["values"][0]; return self
        def batchUpdate(self, **kw): self.sent = kw["body"]["data"][0]["values"][0]; return self

    head = sheets.TABS["Станции"]
    row = {"id": "S999", "телефон": "+359000000001, +359000000002", "мощность кВт": 1500, "год": "неизвестно",
           "заголовок": "=== ПРОДАВАМ ФЕЦ ===", "продавец": '=IMAGE("https://evil.example/x.png")', "приоритет": "-"}
    for write in (lambda api: sheets.append(api, "x", "Станции", [row]),
                  lambda api: sheets.update(api, "x", "Станции", [{**row, "_row": 5}])):
        api = Api()
        write(api)
        sent = dict(zip(head, api.sent))
        assert sent["телефон"] == "'+359000000001, +359000000002"
        assert sent["мощность кВт"] == 1500 and sent["год"] == "неизвестно" and sent["id"] == "S999"
        assert sent["заголовок"] == "'=== ПРОДАВАМ ФЕЦ ===" and sent["продавец"].startswith("'=")   # text, never a formula
        assert sent["приоритет"] == "'-"


def test_next_id_follows_the_largest_not_the_count():
    assert sheets.next_id(["D001", "D002", "D004"]) == "D005" and sheets.next_id([]) == "D001"

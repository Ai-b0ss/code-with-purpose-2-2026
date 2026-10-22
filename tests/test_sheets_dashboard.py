"""Google dashboard request shape and live formulas, without network calls."""
import unittest

from backend.sheets_dashboard import CHART_TITLES, MARKER, VERSION, _layout, ensure_dashboard


class Response:
    ok = True

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class Session:
    def __init__(self, marker=None):
        self.marker = marker
        self.posts = []

    def get(self, *args, **kwargs):
        return Response({"values": [[self.marker, "", "", "", "", "", "", VERSION]] if self.marker else []})

    def post(self, url, **kwargs):
        self.posts.append(kwargs["json"]["requests"])
        if "addSheet" in self.posts[-1][0]:
            return Response({"replies": [{"addSheet": {"properties": {"sheetId": 9}}}]})
        return Response({"replies": [{} for _ in self.posts[-1]]})


class DashboardTests(unittest.TestCase):
    def test_creates_formula_tables_ranking_and_three_charts(self):
        session = Session()
        ensure_dashboard(session, "https://example.invalid/sheet", "Заявки",
                         [{"properties": {"sheetId": 7, "title": "Заявки"}}])
        self.assertEqual(len(session.posts), 2)
        requests = session.posts[1]
        cells = requests[0]["updateCells"]["rows"]
        self.assertEqual(cells[0]["values"][0]["userEnteredValue"], {"stringValue": MARKER})
        self.assertIn("COUNTIFS", cells[9]["values"][1]["userEnteredValue"]["formulaValue"])
        self.assertIn("SORT(FILTER", cells[28]["values"][0]["userEnteredValue"]["formulaValue"])
        self.assertEqual(cells[28]["values"][1], {})
        charts = [request["addChart"]["chart"] for request in requests if "addChart" in request]
        self.assertEqual([chart["spec"]["title"] for chart in charts], list(CHART_TITLES))
        self.assertTrue(all(chart["position"]["overlayPosition"]["anchorCell"]["sheetId"] == 9
                            for chart in charts))
        self.assertTrue(all(chart["position"]["overlayPosition"]["anchorCell"]["columnIndex"] >= 9
                            for chart in charts))
        self.assertEqual([chart["position"]["overlayPosition"]["anchorCell"]["rowIndex"]
                          for chart in charts], [2, 22, 42])
        self.assertEqual(sum("mergeCells" in request for request in requests), 2)

    def test_existing_dashboard_does_not_duplicate_charts(self):
        session = Session(MARKER)
        sheets = [{"properties": {"sheetId": 9, "title": "Статистика"},
                   "charts": [{"spec": {"title": title}} for title in CHART_TITLES]}]
        ensure_dashboard(session, "https://example.invalid/sheet", "Заявки", sheets)
        self.assertEqual(session.posts, [])

    def test_existing_incomplete_dashboard_repairs_missing_chart(self):
        session = Session(MARKER)
        sheets = [{"properties": {"sheetId": 9, "title": "Статистика"},
                   "charts": [{"spec": {"title": CHART_TITLES[0]}}]}]
        ensure_dashboard(session, "https://example.invalid/sheet", "Заявки", sheets)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(len(session.posts[0]), 2)

    def test_score_formula_keeps_unknown_distinct_from_zero(self):
        rows = _layout("Заявки")
        self.assertEqual(rows[9][0], 0)
        self.assertEqual(rows[13][1], "=F5")
        self.assertIn("VALUE(", rows[28][0])


if __name__ == "__main__":
    unittest.main()

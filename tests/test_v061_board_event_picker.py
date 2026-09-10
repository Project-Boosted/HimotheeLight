from pathlib import Path
import unittest


class BoardEventPickerUiTests(unittest.TestCase):
    def test_board_event_picker_contains_board_manager_events(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "web" / "index.html").read_text(encoding="utf-8")
        js = (root / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="triggerBoardEvent"', html)
        for event in [
            "Starting", "Started", "Stopping", "Stopped", "Throw detected",
            "Takeout started", "Takeout finished", "Manual reset",
            "Calibration started", "Calibration finished", "Calibration failed",
        ]:
            self.assertIn(f'value="{event}"', html)
        self.assertIn("triggerType === 'board_event' ? $('triggerBoardEvent').value", js)
        self.assertIn("$('triggerBoardEvent').classList.toggle('hidden', !boardEvent)", js)


if __name__ == "__main__":
    unittest.main()

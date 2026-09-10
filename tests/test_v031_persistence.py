import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from himotheelight.config_store import ConfigStore, trigger_template
from himotheelight.logging_buffer import MemoryLogHandler
from himotheelight.server import build_server


class TriggerEnabledPersistenceTest(unittest.TestCase):
    def test_multiple_trigger_enabled_flags_survive_api_reload_and_disk_reload(self):
        temp = tempfile.TemporaryDirectory()
        app = None
        try:
            data_dir = Path(temp.name)
            store = ConfigStore(data_dir)
            t1 = trigger_template("One", "dart", value="T20", enabled=False)
            t2 = trigger_template("Two", "dart", value="D20", enabled=False)
            t3 = trigger_template("Three", "visit_exact", value="180", enabled=False)
            store.update(lambda cfg: cfg.update({
                "triggers": [t1, t2, t3],
                "devices": [],
                "autodarts": {**cfg["autodarts"], "enabled": False, "auto_connect": False},
            }))

            web_dir = Path(__file__).resolve().parents[1] / "web"
            app = build_server("127.0.0.1", 0, store, web_dir, MemoryLogHandler(100))
            app.context.start()
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{app.server_address[1]}"

            def request(method, path, body=None):
                data = None if body is None else json.dumps(body).encode("utf-8")
                req = urllib.request.Request(
                    base + path,
                    data=data,
                    method=method,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=2) as response:
                    return json.loads(response.read().decode("utf-8"))

            # This mirrors the v0.3.1 enable switch: PATCH only the selected rule,
            # then move on to another trigger without rewriting its neighbours.
            for trigger in (t1, t2, t3):
                result = request("PATCH", f"/api/triggers/{trigger['id']}", {"enabled": True})
                self.assertTrue(result["trigger"]["enabled"])

            config = request("GET", "/api/config")["config"]
            self.assertEqual([t["enabled"] for t in config["triggers"]], [True, True, True])

            # Verify persistence is not just process memory.
            reloaded = ConfigStore(data_dir).get()
            self.assertEqual([t["enabled"] for t in reloaded["triggers"]], [True, True, True])
        finally:
            if app is not None:
                try:
                    app.context.stop(); app.shutdown(); app.server_close()
                except Exception:
                    pass
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()

"""The standalone board: free-port serving, vendored fonts (no request to Google), site folder.
Ported from the bluey-experiment copy (82ef158). Nothing real is launched; the server runs on localhost."""
import importlib.util, json, re, socket, subprocess, sys, tempfile, time, unittest, urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
SWARM = SKILL / "scripts/swarm.py"


def _load_swarm():
    spec = importlib.util.spec_from_file_location("swarm_under_test", SWARM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _busy_socket():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sw = _load_swarm()

    def test_busy_port_moves_to_a_free_one(self):
        s, port = _busy_socket()
        try:
            httpd = self.sw._make_server(lambda *a, **k: None, port, "127.0.0.1")
            self.assertNotEqual(httpd.server_port, port)
            httpd.server_close()
        finally:
            s.close()

    def test_port_zero_and_exhausted_candidates_fall_back_to_any_free_port(self):
        httpd = self.sw._make_server(lambda *a, **k: None, 0, "127.0.0.1")
        self.assertGreater(httpd.server_port, 0)
        httpd.server_close()
        s, port = _busy_socket()
        try:   # a single candidate that is busy still comes up somewhere
            h2 = self.sw._make_server(lambda *a, **k: None, port, "127.0.0.1", tries=1)
            self.assertNotIn(h2.server_port, (0, port))
            h2.server_close()
        finally:
            s.close()


class AssetTest(unittest.TestCase):
    def test_dashboard_makes_no_external_request(self):
        page = (SKILL / "assets/dashboard.html").read_text()
        head = page[:page.index("</style>")]
        self.assertNotIn("googleapis", page)
        self.assertNotIn("gstatic", page)
        self.assertIn('href="fonts/fonts.css"', head)
        self.assertEqual(re.findall(r'(?:src|href)=["\']https?://', page), [])

    def test_vendored_fonts_are_complete(self):
        css = (SKILL / "assets/fonts/fonts.css").read_text()
        files = re.findall(r"url\(([^)]+)\)", css)
        self.assertTrue(files)
        for f in files:
            self.assertFalse(f.startswith("http"), f)
            self.assertEqual((SKILL / "assets/fonts" / f).read_bytes()[:4], b"wOF2", f)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.ops = tmp / "ops"
        (self.ops / "data").mkdir(parents=True)
        (self.ops / "swarm.toml").write_text(
            '[project]\nname = "board test"\nrepo = "%s"\nworktrees = "%s"\nforbidden = []\n'
            % (tmp / "repo", tmp / "wt"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_site_folder_has_page_data_and_fonts(self):
        out = self.ops / "site"
        r = subprocess.run([sys.executable, str(SWARM), "--config", str(self.ops / "swarm.toml"), "site",
                            "--out", str(out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("index.html", "meta.json", "events.json", "fonts/fonts.css"):
            self.assertTrue((out / n).is_file(), n)
        html = (out / "index.html").read_text()
        self.assertIn("fonts/fonts.css", html)
        self.assertNotIn("googleapis", html)
        for f in re.findall(r"url\(([^)]+)\)", (out / "fonts/fonts.css").read_text()):
            self.assertTrue((out / "fonts" / f).is_file(), f)

    def test_watch_serve_comes_up_on_a_free_port_and_serves_page_and_fonts(self):
        s, busy = _busy_socket()
        p = subprocess.Popen([sys.executable, str(SWARM), "--config", str(self.ops / "swarm.toml"), "watch",
                              "--serve", str(busy), "--no-claude", "--every", "60"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            line = p.stdout.readline()
            m = re.search(r"at (http://127\.0\.0\.1:(\d+)/)", line)
            self.assertIsNotNone(m, line)
            url, port = m.group(1), int(m.group(2))
            self.assertNotEqual(port, busy)
            self.assertIn("busy or blocked", line)
            self.assertEqual((self.ops / "out" / "board.url").read_text().strip(), url)
            body = None
            for _ in range(40):   # the first pass writes the folder a moment after the server starts
                try:
                    body = urllib.request.urlopen(url, timeout=2).read().decode()
                    break
                except Exception:
                    time.sleep(0.25)
            self.assertIn("fonts/fonts.css", body or "")
            css = urllib.request.urlopen(url + "fonts/fonts.css", timeout=2).read().decode()
            first = re.search(r"url\(([^)]+)\)", css).group(1)
            self.assertEqual(urllib.request.urlopen(url + "fonts/" + first, timeout=2).read()[:4], b"wOF2")
            self.assertTrue(json.loads(urllib.request.urlopen(url + "meta.json", timeout=2).read()))
        finally:
            p.terminate()
            p.wait(timeout=10)
            s.close()


if __name__ == "__main__":
    unittest.main()

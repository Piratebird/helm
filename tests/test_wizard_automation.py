"""The setup wizard's full-automation prompts, against a real local HTTP server.

The server acts like Jackett, Prowlarr and qBittorrent closely enough for the
wizard's checks: API keys, the Jackett admin password, and qBittorrent's SID cookie.
"""

import builtins
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

import helm.config.config_wizard as wizard
from helm.config.secret_manager import get_secret
from helm.config.settings import load_settings

JACKETT_KEY = "jackett-key-123456"
PROWLARR_KEY = "prowlarr-key-123456"
ADMIN_PASSWORD = "admin-pass"
QB_USER, QB_PASSWORD = "admin", "qb-pass"


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body="", headers=None):
        data = body.encode()
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def form(self):
        length = int(self.headers.get("Content-Length") or 0)
        return {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode()).items()}

    def do_GET(self):
        url = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        cookie = self.headers.get("Cookie", "")
        if url.path == "/api/v2.0/indexers/all/results/torznab/api":
            return self.reply(200 if query.get("apikey") == JACKETT_KEY else 401, "<indexers/>")
        if url.path == "/api/v2.0/indexers/3dtorrents/config":
            return self.reply(200, "[]" if "Jackett=ok" in cookie else "<html>login</html>")
        if url.path == "/UI/Dashboard":
            return self.reply(200, "<html>dashboard</html>")
        if url.path == "/api/v1/indexer":
            return self.reply(200 if self.headers.get("X-Api-Key") == PROWLARR_KEY else 401, "[]")
        if url.path == "/api/v2/app/version":
            return self.reply(200 if "SID=good" in cookie else 403, "v5.0.0")
        return self.reply(404)

    def do_POST(self):
        form = self.form()
        if self.path == "/UI/Dashboard":
            if form.get("password", "") == ADMIN_PASSWORD:
                return self.reply(302, headers={"Set-Cookie": "Jackett=ok; Path=/", "Location": "/UI/Dashboard"})
            return self.reply(200, "<html>login</html>")
        if self.path == "/api/v2/auth/login":
            if (form.get("username"), form.get("password")) == (QB_USER, QB_PASSWORD):
                return self.reply(200, "Ok.", {"Set-Cookie": "SID=good; Path=/"})
            return self.reply(200, "Fails.")
        return self.reply(404)


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def answers(monkeypatch, tmp_path):
    monkeypatch.setenv("HELM_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HELM_STATE_DIR", str(tmp_path / "state"))
    for key in ("JACKETT_API_KEY", "JACKETT_PASSWORD", "PROWLARR_API_KEY", "QB_PASSWORD", "QB_WEBUI", "JACKETT_URL"):
        monkeypatch.delenv(key, raising=False)
    prompts = []

    def feed(*replies):
        queue = list(replies)

        def fake_input(prompt=""):
            prompts.append(prompt)
            if not queue:
                raise AssertionError(f"unexpected prompt: {prompt!r}")
            return queue.pop(0)

        monkeypatch.setattr(builtins, "input", fake_input)
        return prompts

    return feed


def test_jackett_and_qbittorrent_retry_until_they_validate(server, answers, capsys):
    prompts = answers(
        "y",
        "1",
        # Jackett: wrong key, then right key with a wrong password, then the right password
        server, "bad-key", "",
        "", JACKETT_KEY, "nope",
        "", "", ADMIN_PASSWORD,
        # qBittorrent: wrong password, then the right one
        server, QB_USER, "wrong",
        "", "", QB_PASSWORD,
    )  # fmt: skip
    wizard.run_wizard()
    out = capsys.readouterr().out
    assert "Jackett connection failed: HTTP 401" in out
    assert "Admin Password appears to be incorrect" in out
    assert "Jackett connection and authentication successful!" in out
    assert "qBittorrent connection failed or auth rejected." in out
    assert "qBittorrent connection successful!" in out
    assert "Configuration saved!" in out
    # The masked key is shown once it is known.
    assert any(prompt.startswith("Jackett API Key [jack...3456]") for prompt in prompts)

    settings = load_settings()
    assert settings.get("services.jackett") == server
    assert settings.get("services.qbittorrent") == server
    assert settings.get("search.indexer_manager") == "jackett"
    assert settings.get("search.lite_only") is False
    assert get_secret("JACKETT_API_KEY") == JACKETT_KEY
    assert get_secret("JACKETT_PASSWORD") == ADMIN_PASSWORD
    assert get_secret("QB_PASSWORD") == QB_PASSWORD


def test_prowlarr_path_saves_prowlarr_settings(server, answers, capsys):
    answers("y", "2", server, "bad", "", PROWLARR_KEY, server, QB_USER, QB_PASSWORD)
    wizard.run_wizard()
    out = capsys.readouterr().out
    assert "Prowlarr connection failed: HTTP 401" in out
    assert "Prowlarr connection successful!" in out
    settings = load_settings()
    assert settings.get("services.prowlarr") == server
    assert settings.get("search.indexer_manager") == "prowlarr"
    assert get_secret("PROWLARR_API_KEY") == PROWLARR_KEY


def test_empty_keys_are_asked_again(server, answers, capsys):
    answers("y", "1", server, "", "", "", JACKETT_KEY, ADMIN_PASSWORD, server, QB_USER, "", "", "", QB_PASSWORD)
    wizard.run_wizard()
    out = capsys.readouterr().out
    assert "Jackett API key is required." in out
    assert "qBittorrent password is required." in out


def test_unreachable_services_report_the_error(answers, capsys):
    dead = "http://127.0.0.1:9"  # discard port: nothing listens
    answers("y", "2", dead, PROWLARR_KEY, "q")
    with pytest.raises(SystemExit) as stop:
        wizard.run_wizard()
    assert stop.value.code == 0
    assert "Prowlarr connection failed:" in capsys.readouterr().out
    assert get_secret("PROWLARR_API_KEY") is None


@pytest.mark.parametrize(
    "replies",
    [
        ("y", "1", "quit"),
        ("y", "1", "", "exit"),
        ("y", "1", "", JACKETT_KEY, "q"),
        ("y", "2", "", "q"),
    ],
)
def test_exit_words_abort_without_saving(server, answers, replies, capsys):
    answers(*replies)
    with pytest.raises(SystemExit) as stop:
        wizard.run_wizard()
    assert stop.value.code == 0
    assert load_settings().origin("search.lite_only") == "default"
    assert get_secret("JACKETT_API_KEY") is None


@pytest.mark.parametrize("field", [0, 1, 2])
def test_exit_words_in_qbittorrent_prompts(server, answers, field, capsys):
    qb = [server, QB_USER, QB_PASSWORD]
    qb[field] = "q"
    answers("y", "2", server, PROWLARR_KEY, *qb[: field + 1])
    with pytest.raises(SystemExit):
        wizard.run_wizard()
    assert "Configuration aborted" in capsys.readouterr().out
    assert get_secret("QB_PASSWORD") is None


def test_ctrl_c_aborts_cleanly(answers, monkeypatch, capsys):
    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupt)
    with pytest.raises(SystemExit) as stop:
        wizard.run_wizard()
    assert stop.value.code == 0
    assert "Configuration aborted" in capsys.readouterr().out

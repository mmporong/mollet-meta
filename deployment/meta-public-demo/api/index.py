"""Read-only Vercel entry point for the public Mollet evidence demo."""
from __future__ import annotations

import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.evaluate import evaluate_contract  # noqa: E402
from runtime.schema import contract_digest, validate_contract, validate_observations  # noqa: E402

PUBLIC = ROOT / "public"
DATA = ROOT / "data"
MAX_BODY = 2 * 1024 * 1024
ASSETS = {"/": "index.html", "/app.js": "app.js", "/improvement.js": "improvement.js", "/style.css": "style.css"}
STATUS = {
    "credential_persistence_enabled": False, "credential_saved": False, "configuration_saved": False,
    "preflight_requests_started": 0, "total_external_requests_started": 0,
    "key_configured": False, "credit_confirmed": False,
    "remaining_credit_usd": "0.00", "budget_usd": "0.00", "reserved_usd": "0.00",
    "available_budget_usd": "0.00", "enabled": False,
    "blockers": ["Live model calls are disabled in this public evidence demo."],
    "live_requests_started": 0, "video_calls_enabled": False,
}
RESULT_TEXT = {
    "마감 시점 안에 조건을 시작해 필요한 유지 시간 동안 연속으로 충족했습니다.":
        "The condition began before the deadline and remained satisfied for the required hold time.",
    "전체 관측창이 덮였지만 마감 안에 시작해 유지 시간을 채운 충족 구간이 없습니다.":
        "The full observation window was covered, but no qualifying interval began before the deadline and lasted for the required hold time.",
    "짧은 영상, 관측 공백 또는 누락 지표 때문에 위반을 확정할 수 없습니다.":
        "A short clip, observation gap, or missing metric prevents a conclusive violation verdict.",
    "트리거 이후 관측 표본이 없습니다.": "No observations are available after the trigger.",
    "FAIL 판정에 필요한 전체 관측창이 연속적으로 덮이지 않았습니다.":
        "The continuous observation window required for a FAIL verdict is incomplete.",
    "승인 기록은 로컬 의미 검토 또는 자동 테스트 표식이며, 사람 인증이나 GPU 실행 권한이 아닙니다.":
        "Review records mark local semantic review or automated testing; they do not authorize a person, GPU work, or execution.",
}


def _english_result(result: dict) -> dict:
    normalized = dict(result)
    normalized["reason"] = RESULT_TEXT.get(normalized.get("reason"), normalized.get("reason"))
    normalized["warnings"] = [RESULT_TEXT.get(item, item) for item in normalized.get("warnings", [])]
    return normalized


def _read_json(name: str) -> dict:
    value = json.loads((DATA / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _request_route(raw_path: str) -> str:
    url = urlsplit(raw_path)
    query = parse_qs(url.query)
    if "asset" in query:
        return "/" if query["asset"][0] == "index.html" else "/" + query["asset"][0]
    if "api" in query:
        return "/api/" + query["api"][0].lstrip("/")
    if "media" in query:
        return "/media/" + query["media"][0].lstrip("/")
    return url.path


class handler(BaseHTTPRequestHandler):
    server_version = "MolletPublic/1.0"

    def log_message(self, format: str, *args) -> None:
        pass

    def _headers(self, status: int, content_type: str, length: int = 0, extra: dict | None = None) -> None:
        self.send_response(status)
        headers = {
            "Content-Type": content_type, "Content-Length": str(length),
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cache-Control": "public, max-age=300" if status == 200 and not content_type.startswith("application/json") else "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "media-src 'self' https://cdn.jsdelivr.net; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            ), **(extra or {}),
        }
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()

    def _json(self, value: dict, status: int = 200) -> None:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(raw))
        self.wfile.write(raw)

    def _error(self, message: str, status: int) -> None:
        self._json({"error": message, "public_demo": True}, status)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        route = _request_route(self.path)
        try:
            if route == "/api/meta/status":
                self._json({"status": STATUS}); return
            if route == "/api/recovery/saved":
                self._json(_read_json("recovery.json")); return
            if route == "/api/examples":
                self._json(_read_json("examples.json")); return
            if route == "/api/example":
                example_id = parse_qs(url.query).get("id", ["h1-up"])[0]
                allowed = {item["id"] for item in _read_json("examples.json")["examples"]}
                if example_id not in allowed:
                    self._error("Unknown example.", 404); return
                self._json(_read_json(f"example-{example_id}.json")); return
            if route.startswith("/media/"):
                media = _read_json("media.json").get(route.removeprefix("/media/"))
                if not media:
                    self._error("Unknown media asset.", 404); return
                self._headers(302, "text/plain; charset=utf-8", 0, {
                    "Location": media["url"], "Cache-Control": "public, max-age=31536000, immutable"})
                return
            asset = ASSETS.get(route)
            if asset is None:
                self._error("Route not found.", 404); return
            path = PUBLIC / asset
            raw = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix in {".html", ".js", ".css"}:
                mime += "; charset=utf-8"
            self._headers(200, mime, len(raw)); self.wfile.write(raw)
        except (OSError, ValueError, KeyError, TypeError):
            self._error("The public evidence package could not be read.", 500)

    def do_POST(self) -> None:
        route = _request_route(self.path)
        if route not in {"/api/validate", "/api/review", "/api/evaluate"}:
            self._error("This public demo is read-only. Model, account, analysis, and training endpoints are disabled.", 403)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._error("Content-Type must be application/json.", 415); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                self._error("Request body is empty or too large.", 413); return
            value = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict): raise ValueError
            if route == "/api/validate":
                self._json({"contract": validate_contract(value["contract"])}); return
            if route == "/api/review":
                contract = validate_contract(value["contract"])
                self._json({"approval": {"contract_sha256": contract_digest(contract),
                    "actor": "public_browser_review", "scope": "local_semantic_review"}}); return
            observations = validate_observations(value["observations"])
            contract = validate_contract(value["contract"])
            result = evaluate_contract(contract, observations, value.get("approval"))
            result = _english_result(result)
            result["input_provenance"] = observations["provenance"]
            result["model_called"] = False
            self._json({"result": result})
        except (ValueError, KeyError, TypeError, OverflowError):
            self._error("The contract or evidence payload is invalid.", 400)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8795
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Mollet public demo: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

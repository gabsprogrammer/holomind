import json
import urllib.error
import urllib.parse
import urllib.request


class MemoryBridge:
    def __init__(self, base_url="http://127.0.0.1:8787", timeout_s=1.2, action_timeout_s=120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.action_timeout_s = max(float(action_timeout_s or 0.0), float(timeout_s))

    def _request_json(self, path, method="GET", payload=None, timeout_s=None):
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=(self.timeout_s if timeout_s is None else timeout_s)) as response:
            charset = response.headers.get_content_charset("utf-8")
            raw = response.read().decode(charset)
            return json.loads(raw)

    def fetch_notes(self):
        try:
            payload = self._request_json("/memory/graph")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return None, None

        nodes = payload.get("nodes", [])
        notes = {}

        for node in nodes:
            title = str(node.get("title") or "Untitled").strip() or "Untitled"
            if title in notes:
                suffix = 2
                while f"{title} ({suffix})" in notes:
                    suffix += 1
                title = f"{title} ({suffix})"

            notes[title] = {
                "info": node.get("info", "Sem descricao."),
                "content": node.get("content", "").strip(),
                "snippets": list(node.get("snippets", []))[:6],
                "meta": dict(node.get("meta", {})),
            }

        meta = {
            "updatedAt": payload.get("updatedAt"),
            "nodeCount": payload.get("nodeCount", len(notes)),
        }

        return notes, meta

    def request_summary(self, chat_id, send=True):
        if not chat_id:
            return None

        path = f"/memory/summaries/{urllib.parse.quote(chat_id, safe='')}"
        try:
            payload = self._request_json(path, method="POST", payload={"send": bool(send)}, timeout_s=self.action_timeout_s)
            return payload.get("summary")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return None

    def request_reply_suggestion(self, chat_id, incoming_text):
        if not chat_id or not incoming_text:
            return None

        path = f"/memory/reply/{urllib.parse.quote(chat_id, safe='')}"
        payload = {"incomingText": str(incoming_text)}
        try:
            data = self._request_json(path, method="POST", payload=payload, timeout_s=self.action_timeout_s)
            suggestion = data.get("suggestion")
            return str(suggestion).strip() if suggestion else None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return None

    def _error_message(self, error):
        if isinstance(error, urllib.error.HTTPError):
            body = ""
            try:
                body = error.read().decode("utf-8", errors="replace").strip()
            except Exception:
                body = ""
            if body:
                try:
                    payload = json.loads(body)
                    msg = payload.get("error") or payload.get("message")
                    if msg:
                        return str(msg)
                except json.JSONDecodeError:
                    return body[:220]
            code = getattr(error, "code", None)
            return f"http error {code}" if code else "http error"
        if isinstance(error, urllib.error.URLError):
            reason = getattr(error, "reason", None)
            if reason:
                return f"network error: {reason}"
            return "network error"
        if isinstance(error, TimeoutError):
            return "timeout contacting backend"
        if isinstance(error, json.JSONDecodeError):
            return "invalid backend json response"
        return str(error) or "unknown error"

    def send_message_result(self, chat_id, text, as_audio=True, profile_id=None, language="pt", fallback_to_text=True):
        if not chat_id or not text:
            return False, "chat_id/text required"

        path = f"/memory/send/{urllib.parse.quote(chat_id, safe='')}"
        base_payload = {
            "text": str(text),
            "language": str(language or "pt"),
        }
        if profile_id:
            base_payload["profileId"] = str(profile_id)

        first_payload = dict(base_payload)
        first_payload["asAudio"] = bool(as_audio)

        try:
            response = self._request_json(path, method="POST", payload=first_payload, timeout_s=self.action_timeout_s)
            if bool(response.get("ok")):
                mode = str(response.get("mode") or ("audio" if as_audio else "text"))
                warn = response.get("warning")
                if warn:
                    return True, f"sent ({mode}) - {str(warn)[:140]}"
                return True, f"sent ({mode})"
            return False, str(response.get("error") or "backend returned ok=false")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as first_error:
            first_msg = self._error_message(first_error)
            if not (as_audio and fallback_to_text):
                return False, first_msg

            second_payload = dict(base_payload)
            second_payload["asAudio"] = False
            try:
                response = self._request_json(path, method="POST", payload=second_payload, timeout_s=self.action_timeout_s)
                if bool(response.get("ok")):
                    return True, f"voice failed ({first_msg}) -> sent as text fallback"
                second_msg = str(response.get("error") or "backend returned ok=false")
                return False, f"voice failed ({first_msg}) | text fallback failed ({second_msg})"
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as second_error:
                second_msg = self._error_message(second_error)
                return False, f"voice failed ({first_msg}) | text fallback failed ({second_msg})"

    def send_message(self, chat_id, text, as_audio=True, profile_id=None, language="pt"):
        ok, _message = self.send_message_result(
            chat_id,
            text,
            as_audio=as_audio,
            profile_id=profile_id,
            language=language,
            fallback_to_text=True,
        )
        return ok

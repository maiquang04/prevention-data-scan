"""Talks to the model running in LM Studio.

LM Studio serves an OpenAI-compatible API on http://127.0.0.1:1234/v1 once the server
is started under Developer > Start Server. Standard library only, so this uses
urllib rather than the openai or requests packages.
"""
import http.client
import json
import re
import sys
import urllib.error
import urllib.request

from config import LMSTUDIO_URL, MODEL_MAIN, NO_THINK_MODELS, TIMEOUT_S

NOT_RUNNING = ("LM Studio's server is not running. "
               "In LM Studio open Developer and click Start Server.")
DROPPED = "LM Studio stopped answering part-way through. Try the question again."


class LLMError(Exception):
    """Anything that stopped us getting a reply out of the model."""


def list_models(timeout=TIMEOUT_S):
    """The model ids the server is offering."""
    try:
        with urllib.request.urlopen(LMSTUDIO_URL + "/models", timeout=timeout) as r:
            body = json.load(r)
    except urllib.error.HTTPError as err:
        raise LLMError("LM Studio returned %s listing models: %s"
                       % (err.code, err.read().decode("utf-8", "replace"))) from err
    except (urllib.error.URLError, ConnectionError, TimeoutError) as err:
        raise LLMError(NOT_RUNNING) from err
    return [m["id"] for m in body.get("data", [])]


def model_states():
    """Which models are loaded into memory right now, as {id: "loaded" | "not-loaded"}.

    Uses LM Studio's own REST API rather than the OpenAI-style one, which lists what
    could be loaded but not what is. A model that is not loaded still answers, because
    LM Studio loads it on first use, but that first answer takes much longer, which is
    worth knowing before a demo. Returns {} if this LM Studio build has no such API.
    """
    url = LMSTUDIO_URL.rsplit("/v1", 1)[0] + "/api/v0/models"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            body = json.load(r)
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
        return {}
    return {m.get("id"): m.get("state") for m in body.get("data", []) if m.get("id")}


def strip_thinking(text):
    """Drop a hybrid model's <think> block, so only its answer is left.

    A model cut off mid-thought leaves an opening tag with no closing one, and
    everything after it is thinking rather than answer, so it goes too.
    """
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "<think>" in text:
        text = text.split("<think>", 1)[0]
    return text.strip()


def _read_stream(response, on_text):
    """Collect a streamed reply, telling on_text the whole text so far after each piece.

    LM Studio streams the OpenAI way: lines of "data: {json}", each carrying the next
    few characters in choices[0].delta.content, ending with "data: [DONE]".
    """
    text = ""
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices") or [{}]
        piece = (choices[0].get("delta") or {}).get("content")
        if piece:
            text += piece
            on_text(text)
    return text


def chat(messages, model=MODEL_MAIN, temperature=0.0, max_tokens=512, schema=None,
         on_text=None):
    """Send a conversation, return the reply text with any thinking removed.

    `schema` is a JSON schema the reply must fit. LM Studio enforces it while the
    model writes, so the reply comes back as JSON of that shape. Whether it also
    enforces `enum` lists is checked by the self-test at the bottom of this file.

    `on_text`, if given, streams the reply: it is called with the whole reply so far
    each time a little more arrives, which is how the search page shows the model's
    reasoning as it is written. Anything on_text raises is passed straight back to the
    caller, so a caller can stop the model part-way by raising.
    """
    if model in NO_THINK_MODELS:
        # Appended to the last user message rather than the system one: that is where
        # Qwen's own documentation puts the switch.
        messages = [dict(m) for m in messages]
        for m in reversed(messages):
            if m.get("role") == "user":
                m["content"] = (m.get("content") or "") + " /no_think"
                break

    body = {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "tags", "strict": True,
                                                   "schema": schema}}
    if on_text is not None:
        body["stream"] = True

    request = urllib.request.Request(
        LMSTUDIO_URL + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    connected = False
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as r:
            connected = True
            if on_text is not None:
                return strip_thinking(_read_stream(r, on_text))
            reply = json.load(r)
    except urllib.error.HTTPError as err:
        # LM Studio explains a rejected response_format in the body, so keep it.
        detail = err.read().decode("utf-8", "replace")
        raise LLMError("LM Studio returned %s: %s" % (err.code, detail)) from err
    except (urllib.error.URLError, ConnectionError, TimeoutError,
            http.client.HTTPException) as err:
        raise LLMError(DROPPED if connected else NOT_RUNNING) from err

    try:
        return strip_thinking(reply["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as err:
        raise LLMError("Unexpected reply from LM Studio: %s" % json.dumps(reply)[:400]) from err


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("models:", list_models())

    colours = {"type": "object",
               "properties": {"colour": {"type": "string",
                                         "enum": ["red", "green", "blue"]}},
               "required": ["colour"]}
    answer = chat([{"role": "user", "content": "Reply with the colour purple."}],
                  schema=colours)
    print("reply:", answer)
    try:
        honoured = json.loads(answer).get("colour") in ("red", "green", "blue")
    except json.JSONDecodeError:
        honoured = False
    print("enum honoured:", honoured)
    print("strip_thinking:", strip_thinking("<think>x</think> {}"))

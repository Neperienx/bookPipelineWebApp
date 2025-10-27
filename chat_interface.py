"""Minimal web chat interface for the local TextGenerator model.

This module exposes a small Flask application that mimics the
single-session conversational workflow of ChatGPT.  Messages are stored in
the user's session so refreshing the page keeps the conversation intact until
it is cleared.  The underlying responses are produced by the local
``TextGenerator`` class defined in :mod:`text_generator`.

Run the application with ``flask --app chat_interface run`` after exporting
``LOCAL_GPT_MODEL_PATH`` (and an optional ``FLASK_SECRET_KEY``).
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List

from flask import Flask, redirect, render_template, request, session, url_for

from text_generator import TextGenerator

# Flask session requires a secret key.  Use an environment variable so the
# application can be run without editing source code.
DEFAULT_SECRET = "dev-secret-key-change-me"

# ``TextGenerator`` is expensive to initialise, so cache a single instance per
# process.  It loads lazily on the first request that needs it.
_generator: TextGenerator | None = None


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", DEFAULT_SECRET)

    @app.route("/", methods=["GET", "POST"])
    def chat() -> str:
        history = session.setdefault("chat_history", [])
        error = None

        if request.method == "POST":
            if "reset" in request.form:
                session.pop("chat_history", None)
                return redirect(url_for("chat"))

            user_message = request.form.get("message", "").strip()
            if user_message:
                history.append({"role": "user", "content": user_message})
                try:
                    generator = _get_generator()
                    assistant_reply = generator.generate_response(
                        _build_prompt(history)
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    error = f"The local model could not generate a reply: {exc}"
                    history.pop()  # remove the user message to avoid confusing UX
                else:
                    history.append(
                        {"role": "assistant", "content": assistant_reply or "(no reply)"}
                    )
                session.modified = True
            else:
                error = "Please enter a message before sending."

        return render_template("chat.html", history=history, error=error)

    return app


def _get_generator() -> TextGenerator:
    """Return a cached ``TextGenerator`` instance."""

    global _generator
    if _generator is None:
        model_path = os.environ.get("LOCAL_GPT_MODEL_PATH")
        if not model_path:
            raise RuntimeError(
                "Set the LOCAL_GPT_MODEL_PATH environment variable to the directory "
                "containing your local Hugging Face model."
            )

        _generator = TextGenerator(model_path)
    return _generator


def _build_prompt(history: Iterable[Dict[str, str]]) -> str:
    """Construct a conversation prompt from the stored history."""

    prompt_lines: List[str] = []
    for message in history:
        if message["role"] == "user":
            prefix = "User"
        else:
            prefix = "Assistant"
        prompt_lines.append(f"{prefix}: {message['content']}")
    prompt_lines.append("Assistant:")
    return "\n".join(prompt_lines)


# Allow ``python chat_interface.py`` to run the development server directly.
if __name__ == "__main__":
    application = create_app()
    application.run(debug=True)

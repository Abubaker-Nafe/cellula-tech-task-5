import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"))

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMError(RuntimeError):
    """A configuration, connection, or model response error."""


def call_llm(messages: list[dict[str, str]], max_tokens: int = 1200,) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "").strip()

    if not api_key or not model:
        raise LLMError("Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env.")

    try:
        response = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": max_tokens,
            },
            timeout=60.0,
        )
        response.raise_for_status()

    except httpx.TimeoutException as exc:
        raise LLMError("The OpenRouter request timed out. Try again.") from exc

    except httpx.HTTPStatusError as exc:
        message = f"OpenRouter HTTP {exc.response.status_code}"

        try:
            error = exc.response.json()["error"]
            detail = (
                error.get("message")
                if isinstance(error, dict)
                else str(error)
            )
            if detail:
                message += f": {detail}"
        except (ValueError, KeyError, TypeError):
            pass

        raise LLMError(message) from exc

    except httpx.RequestError as exc:
        raise LLMError(
            "Could not reach OpenRouter. "
            "Check your connection and API configuration."
        ) from exc

    try:
        choice = response.json()["choices"][0]
        content = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError(
            "OpenRouter returned an unexpected response."
        ) from exc

    if choice.get("finish_reason") == "length":
        raise LLMError(
            "The model response was truncated. Increase max_tokens."
        )

    if not isinstance(content, str) or not content.strip():
        raise LLMError("The model returned an empty text response.")

    return content.strip()


if __name__ == "__main__":
    try:
        reply = call_llm(
            [
                {
                    "role": "user",
                    "content": "Reply with exactly READY and nothing else.",
                }
            ],
            max_tokens=64,
        )
    except LLMError as exc:
        raise SystemExit(f"Connection failed: {exc}")

    print("Connection: SUCCESS")
    print(f"Reply: {reply}")
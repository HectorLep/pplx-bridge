"""Prueba rapida del puente pplx_bridge.

Verifica el flujo completo sin errores::

    # 1) Arranca el servidor en otra terminal:
    python server.py
    # 2) Ejecuta esta prueba:
    python test_client.py
    python test_client.py --prompt "Resume la fotosintesis en 2 lineas"

Con ``--direct`` se salta el servidor HTTP y se llama a
``browser_client.ask_perplexity`` directamente (util si el servidor no
esta levantado)::

    python test_client.py --direct

Sale con codigo 0 si hay respuesta no vacia, 1 en caso contrario.
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_URL = "http://127.0.0.1:8000/v1/chat/completions"
DEFAULT_PROMPT = "Di exactamente: puente operativo. Y nada mas."
DEFAULT_MODEL = "perplexity-web"


def check_openai_payload(data: dict) -> str:
    """Valida la forma chat.completion y devuelve el contenido."""
    if not isinstance(data, dict):
        raise ValueError(f"respuesta JSON no es objeto: {data!r}")
    choices = data.get("choices")
    if not choices:
        raise ValueError(f"sin 'choices' en la respuesta: {data!r}")
    message = choices[0].get("message", {})
    content = (message.get("content") or "").strip()
    if not content:
        raise ValueError(f"contenido vacio en la respuesta: {data!r}")
    return content


def run_via_server(url: str, prompt: str, model: str, timeout_s: float) -> str:
    import httpx

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(url, json=payload)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    return check_openai_payload(resp.json())


def run_direct(prompt: str, timeout_s: float) -> str:
    try:
        from browser_client import ask_perplexity
    except ImportError:
        from tools.pplx_bridge.browser_client import ask_perplexity  # type: ignore
    content = (ask_perplexity(prompt, timeout_s=timeout_s) or "").strip()
    if not content:
        raise ValueError("browser_client devolvio texto vacio")
    return content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prueba rapida del puente pplx_bridge")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--direct", action="store_true",
        help="llamar a browser_client sin pasar por el servidor HTTP",
    )
    args = parser.parse_args(argv)

    mode = "directo (browser_client)" if args.direct else f"servidor ({args.url})"
    print(f"[test] modo: {mode}")
    print(f"[test] prompt: {args.prompt!r}")
    try:
        if args.direct:
            content = run_direct(args.prompt, args.timeout)
        else:
            content = run_via_server(args.url, args.prompt, args.model, args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[test] FALLO: {exc}")
        return 1
    preview = content if len(content) <= 500 else content[:500] + "..."
    print(f"[test] OK: respuesta de {len(content)} caracteres")
    print(f"[test] contenido: {preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

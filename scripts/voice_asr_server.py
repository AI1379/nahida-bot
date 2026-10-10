# /// script
# requires-python = ">=3.12"
# dependencies = ["fastapi>=0.109", "uvicorn>=0.27", "faster-whisper>=1.1,<2"]
# ///
"""Run with: uv run scripts/voice_asr_server.py --model small

Dependencies live in uv's isolated script environment. A model name downloads
its weights on first run; --model can instead point to an existing local model.
"""

import argparse
from pathlib import Path
import secrets
import sys

# Allow direct script execution without installing the full Bot and its plugins.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="small")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()

    import uvicorn
    from nahida_bot.voice.asr import FasterWhisperAsrProvider
    from nahida_bot.voice.asr_host import create_asr_app

    provider = FasterWhisperAsrProvider(args.model, threads=args.threads)
    token = secrets.token_urlsafe(32)
    print(f"ASR endpoint: http://{args.host}:{args.port}/v1/asr/transcriptions")
    print(f"ASR token (this run only): {token}")
    uvicorn.run(create_asr_app(provider, token), host=args.host, port=args.port)


if __name__ == "__main__":
    main()

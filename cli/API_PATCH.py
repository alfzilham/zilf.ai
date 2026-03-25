"""
PATCH â€” tambahkan ini ke agent/api.py
======================================

Tambahkan 3 hal:
  1. import os, argparse  (di bagian atas file)
  2. Endpoint /health     (di dalam FastAPI app)
  3. Argparse --port      (di bagian if __name__ == "__main__")
"""

import os
import argparse

# â”€â”€ 1. Tambahkan endpoint ini ke FastAPI app kamu â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.get("/health")
def health():
    return {"status": "ok", "agent": "zilf.ai", "version": "1.0.0"}


# â”€â”€ 2. Ganti bagian if __name__ == "__main__" dengan ini â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="zilf.ai API Server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AGENT_PORT", 8000)),
        help="Port to run the server on",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
    )
    args = parser.parse_args()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",   # suppress noise saat dipakai dari CLI
    )

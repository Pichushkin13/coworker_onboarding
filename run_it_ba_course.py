"""Run the training application after safely adding the IT BA onboarding course."""
import os
from http.server import ThreadingHTTPServer

from app import Handler, init_db
from seed_it_ba_course import seed


if __name__ == "__main__":
    init_db()
    seed()
    host = "127.0.0.1"
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Training app with IT BA course started: http://{host}:{port}")
    server.serve_forever()

"""Disposable authenticated loopback Radicale for local Bot interoperability tests.

Run: uv run python scripts/caldav_local_server.py --port 5232
The generated credential is for synthetic local data only. Ctrl-C stops the server.
"""

import argparse
from contextlib import AbstractContextManager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any

import httpx


class LocalCalDAV(AbstractContextManager):
    def __init__(self, directory: Path, *, port: int = 0, password: str = "") -> None:
        self.directory = directory
        saved = directory / "local-connection.json"
        if not password and saved.is_file():
            password = json.loads(saved.read_text(encoding="utf-8"))["password"]
        self.password = password or secrets.token_urlsafe(24)
        self.username = "nahida-test"
        if not port:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/"
        self.port = port
        self.process: subprocess.Popen | None = None
        self.log: Any = None

    def __enter__(self) -> "LocalCalDAV":
        self.directory.mkdir(parents=True, exist_ok=True)
        users = self.directory / "test-users"
        users.write_text(f"{self.username}:{self.password}\n", encoding="utf-8")
        config = self.directory / "radicale.conf"
        config.write_text(
            f"[server]\nhosts = 127.0.0.1:{self.port}\n[auth]\ntype = htpasswd\n"
            f"htpasswd_filename = {users.as_posix()}\nhtpasswd_encryption = plain\ndelay = 0\n"
            f"[rights]\ntype = owner_only\n[storage]\nfilesystem_folder = {(self.directory / 'storage').as_posix()}\n"
            "[logging]\nlevel = warning\n",
            encoding="utf-8",
        )
        self.log = (self.directory / "server.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "radicale", "--config", str(config)],
            cwd=self.directory,
            stdout=self.log,
            stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError("Local Radicale exited; see server.log")
                try:
                    with self.client() as client:
                        if client.request("OPTIONS", "/").status_code < 500:
                            return self
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            raise TimeoutError("Local Radicale did not start")
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *args: Any) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.url,
            auth=(self.username, self.password),
            trust_env=False,
            timeout=5,
        )

    def create_collection(self, name: str, kind: str) -> str:
        if not name.replace("-", "").isalnum() or kind not in {"VTODO", "VEVENT"}:
            raise ValueError("Invalid test collection")
        path = f"/{self.username}/{name}/"
        body = f'<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"><D:set><D:prop><D:displayname>{name}</D:displayname><C:supported-calendar-component-set><C:comp name="{kind}"/></C:supported-calendar-component-set></D:prop></D:set></C:mkcalendar>'
        with self.client() as client:
            response = client.request(
                "MKCALENDAR",
                path,
                content=body,
                headers={"Content-Type": "application/xml"},
            )
            if response.status_code not in (201, 405):
                response.raise_for_status()
        return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5232)
    parser.add_argument("--directory", type=Path)
    options = parser.parse_args()
    directory = options.directory or Path(
        tempfile.mkdtemp(prefix="nahida-caldav-local-")
    )
    with LocalCalDAV(directory.resolve(), port=options.port) as server:
        tasks = server.create_collection("tasks", "VTODO")
        calendar = server.create_collection("calendar", "VEVENT")
        settings = {
            "url": server.url,
            "username": server.username,
            "password": server.password,
            "tasks": tasks,
            "calendar": calendar,
            "allow_local_http": True,
        }
        credentials = directory / "local-connection.json"
        credentials.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        print(
            f"Local CalDAV: {server.url}\nSynthetic test connection: {credentials.resolve()}",
            flush=True,
        )
        try:
            while server.process and server.process.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

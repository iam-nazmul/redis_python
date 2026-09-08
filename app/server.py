"""A single-threaded TCP server that multiplexes clients with a selector."""

import logging
import selectors
import socket
from dataclasses import dataclass, field

from app import commands, resp
from app.store import Store

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6389
READ_SIZE = 1024

logger = logging.getLogger(__name__)


@dataclass
class Connection:
    """A client socket plus the bytes of a command that has not fully arrived."""

    socket: socket.socket
    address: tuple
    buffer: bytearray = field(default_factory=bytearray)


class Server:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        reuse_port: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.reuse_port = reuse_port
        self.store = Store()
        self.selector = selectors.DefaultSelector()

    def serve_forever(self) -> None:
        with socket.create_server(
            (self.host, self.port), reuse_port=self.reuse_port
        ) as listener:
            # The listening socket is the only one registered without a Connection.
            self.selector.register(listener, selectors.EVENT_READ, data=None)
            logger.info("bedis listening on %s:%s", self.host, self.port)
            try:
                while True:
                    for key, _ in self.selector.select():
                        if key.data is None:
                            self._accept(key.fileobj)
                        else:
                            self._read(key.data)
            except KeyboardInterrupt:
                logger.info("shutting down")
            finally:
                self.selector.close()

    def _accept(self, listener: socket.socket) -> None:
        client, address = listener.accept()
        logger.info("accepted connection from %s", address)
        connection = Connection(client, address)
        self.selector.register(client, selectors.EVENT_READ, data=connection)

    def _close(self, connection: Connection) -> None:
        logger.info("closed connection from %s", connection.address)
        self.selector.unregister(connection.socket)
        connection.socket.close()

    def _read(self, connection: Connection) -> None:
        try:
            data = connection.socket.recv(READ_SIZE)
        except ConnectionError:
            data = b""
        if not data:
            self._close(connection)
            return
        connection.buffer.extend(data)
        try:
            parsed, tail = resp.parse(bytes(connection.buffer))
        except resp.ProtocolError as exc:
            # The stream is out of sync and cannot be resynchronised: hang up.
            logger.warning("protocol error from %s: %s", connection.address, exc)
            self._reply(connection, resp.error(b"ERR Protocol error"))
            self._close(connection)
            return
        connection.buffer = bytearray(tail)
        for args in parsed:
            if not args:
                continue
            if not self._reply(connection, commands.execute(self.store, args)):
                return

    def _reply(self, connection: Connection, reply: bytes) -> bool:
        """Send one reply; returns False once the client has gone away."""
        try:
            connection.socket.sendall(reply)
        except OSError:
            self._close(connection)
            return False
        return True

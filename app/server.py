"""A single-threaded TCP server that multiplexes clients with a selector."""

import logging
import selectors
import socket
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from app import commands, resp
from app.store import Store, WrongTypeError, now_ms

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6389
READ_SIZE = 1024

logger = logging.getLogger(__name__)


@dataclass
class Connection:
    """A client socket, its half-received bytes, and any commands still to run."""

    socket: socket.socket
    address: tuple
    buffer: bytearray = field(default_factory=bytearray)
    # Commands parsed but not yet executed. A blocked client keeps filling this
    # instead of running them, so the order it sent them in is preserved.
    pending: deque = field(default_factory=deque)
    # Per-connection command state, which is where a transaction lives.
    session: commands.Session = field(default_factory=commands.Session)
    blocked: "Waiter | None" = None
    closed: bool = False


@dataclass
class Waiter:
    """A parked client, and the retry that decides when it can be answered."""

    connection: Connection
    retry: Callable[[Store], bytes | None]
    deadline: float | None  # monotonic ms, or None to wait indefinitely


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
        # Ordered by arrival, so the longest-waiting client is served first.
        self.waiters: list[Waiter] = []

    def serve_forever(self) -> None:
        with socket.create_server(
            (self.host, self.port), reuse_port=self.reuse_port
        ) as listener:
            # The listening socket is the only one registered without a Connection.
            self.selector.register(listener, selectors.EVENT_READ, data=None)
            logger.info("bedis listening on %s:%s", self.host, self.port)
            try:
                while True:
                    # Wake for I/O, or at the earliest waiter deadline.
                    for key, _ in self.selector.select(self._next_timeout()):
                        if key.data is None:
                            self._accept(key.fileobj)
                        else:
                            self._read(key.data)
                    self._serve_waiters()
                    self._expire_waiters()
            except KeyboardInterrupt:
                logger.info("shutting down")
            finally:
                self.selector.close()

    # --- connections -----------------------------------------------------

    def _accept(self, listener: socket.socket) -> None:
        client, address = listener.accept()
        logger.info("accepted connection from %s", address)
        connection = Connection(client, address)
        self.selector.register(client, selectors.EVENT_READ, data=connection)

    def _close(self, connection: Connection) -> None:
        if connection.closed:
            return
        connection.closed = True
        if connection.blocked is not None:
            self._forget(connection.blocked)
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
        connection.pending.extend(args for args in parsed if args)
        self._drain(connection)

    def _drain(self, connection: Connection) -> None:
        """Run queued commands until they run out or one blocks."""
        while connection.pending and not connection.closed:
            if connection.blocked is not None:
                return  # stay parked; the rest waits until this client is answered
            reply = commands.execute(
                self.store, connection.pending.popleft(), connection.session
            )
            if isinstance(reply, commands.Block):
                self._block(connection, reply)
            elif not self._reply(connection, reply):
                return

    def _reply(self, connection: Connection, reply: bytes) -> bool:
        """Send one reply; returns False once the client has gone away."""
        try:
            connection.socket.sendall(reply)
        except OSError:
            self._close(connection)
            return False
        return True

    # --- blocking --------------------------------------------------------

    def _block(self, connection: Connection, block: commands.Block) -> None:
        deadline = None if block.timeout == 0 else now_ms() + block.timeout * 1000
        waiter = Waiter(connection, block.retry, deadline)
        connection.blocked = waiter
        self.waiters.append(waiter)

    def _forget(self, waiter: Waiter) -> None:
        waiter.connection.blocked = None
        if waiter in self.waiters:
            self.waiters.remove(waiter)

    def _unblock(self, waiter: Waiter, reply: bytes) -> None:
        self._forget(waiter)
        if self._reply(waiter.connection, reply):
            # Anything the client sent while parked runs now, in order.
            self._drain(waiter.connection)

    def _serve_waiters(self) -> None:
        """Answer parked clients that can now be answered, longest-waiting first."""
        for waiter in list(self.waiters):
            if waiter.connection.closed:
                self._forget(waiter)
                continue
            try:
                reply = waiter.retry(self.store)
            except WrongTypeError:
                # The key now holds something of another type; keep waiting for
                # one that fits rather than failing a command already accepted.
                continue
            if reply is not None:
                self._unblock(waiter, reply)

    def _expire_waiters(self) -> None:
        now = now_ms()
        for waiter in list(self.waiters):
            if waiter.deadline is not None and now >= waiter.deadline:
                self._unblock(waiter, resp.NULL_ARRAY)

    def _next_timeout(self) -> float | None:
        """Seconds until the earliest deadline, or None when nothing is waiting."""
        deadlines = [w.deadline for w in self.waiters if w.deadline is not None]
        if not deadlines:
            return None
        return max((min(deadlines) - now_ms()) / 1000, 0)

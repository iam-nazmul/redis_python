import socket  # noqa: F401
import selectors

selector = selectors.DefaultSelector()
store = {}

def read_line(buffer, start):
    end = buffer.find(b"\r\n", start)
    if end == -1:
        return None, start
    return buffer[start:end], end + 2

def parse_command(buffer):
    # returns (args, rest); args is None when the buffer holds no complete command yet
    line, pos = read_line(buffer, 0)
    if line is None:
        return None, buffer
    if not line.startswith(b"*"):
        # inline command, e.g. typed straight into telnet
        return line.split(), buffer[pos:]
    args = []
    for _ in range(int(line[1:])):
        header, pos = read_line(buffer, pos)
        if header is None:
            return None, buffer
        length = int(header[1:])
        if len(buffer) < pos + length + 2:
            return None, buffer
        args.append(buffer[pos:pos + length])
        pos += length + 2
    return args, buffer[pos:]

def parse_commands(buffer):
    # drains every complete command, keeping any half-received tail for the next read
    commands = []
    while buffer:
        args, rest = parse_command(buffer)
        if args is None:
            break
        commands.append(args)
        buffer = rest
    return commands, buffer

def bulk_string(value):
    return b"$%d\r\n%s\r\n" % (len(value), value)

def wrong_args(command):
    return b"-ERR wrong number of arguments for '%s' command\r\n" % command.lower()

def run_command(args):
    command = args[0].upper()
    if command == b"PING":
        return b"+PONG Nazmul\r\n"
    if command == b"ECHO":
        if len(args) != 2:
            return wrong_args(command)
        return bulk_string(args[1])
    if command == b"SET":
        if len(args) != 3:
            return wrong_args(command)
        store[args[1]] = args[2]
        return b"+OK\r\n"
    if command == b"GET":
        if len(args) != 2:
            return wrong_args(command)
        value = store.get(args[1])
        if value is None:
            return b"$-1\r\n"
        return bulk_string(value)
    return b"-ERR unknown command '%s'\r\n" % args[0]

def accept(server):
    connection, address = server.accept()
    print(f"Accepted connection from {address}")
    # the buffer travels with the connection, so partial commands survive until the next read
    selector.register(connection, selectors.EVENT_READ, data=bytearray())

def close(connection):
    selector.unregister(connection)
    connection.close()

def serve(connection, buffer):
    try:
        data = connection.recv(1024)
    except ConnectionError:
        data = b""
    if not data:
        close(connection)
        return
    buffer.extend(data)
    commands, rest = parse_commands(bytes(buffer))
    buffer.clear()
    buffer.extend(rest)
    for args in commands:
        if args:
            connection.sendall(run_command(args))

def main():
    print("running bedis server")
    # responds to connections at 6379
    with socket.create_server(("localhost", 6389), reuse_port=True) as server:
        selector.register(server, selectors.EVENT_READ, data=None)
        print('Accepting incoming connections')
        while True:
            for key, _ in selector.select():
                if key.data is None:
                    accept(key.fileobj)
                else:
                    serve(key.fileobj, key.data)

if __name__ == "__main__":
    main()

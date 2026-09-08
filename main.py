import socket  # noqa: F401
import threading

def handler(conn):
    while data := conn.recv(1024):
        conn.sendall(b"+PONG Nazmul\r\n")

def main():
    print("running bedis server")
    # responds to connections at 6379
    with socket.create_server(("localhost", 6389), reuse_port=True) as server:
        while True:
            print('Accepting incoming connection')
            connection, _ = server.accept()
            threading.Thread(target=handler, args=(connection,)).start()

if __name__ == "__main__":
    main()
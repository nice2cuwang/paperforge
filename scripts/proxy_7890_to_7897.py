import socket
import threading

LISTEN_HOST = '127.0.0.1'
LISTEN_PORT = 7890
TARGET_HOST = '127.0.0.1'
TARGET_PORT = 7897
BUFFER_SIZE = 65536


def pipe(src, dst):
    try:
        while True:
            data = src.recv(BUFFER_SIZE)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle_client(client_sock):
    target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    target_sock.connect((TARGET_HOST, TARGET_PORT))

    t1 = threading.Thread(target=pipe, args=(client_sock, target_sock), daemon=True)
    t2 = threading.Thread(target=pipe, args=(target_sock, client_sock), daemon=True)
    t1.start()
    t2.start()


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((LISTEN_HOST, LISTEN_PORT))
server.listen(128)

while True:
    client, _ = server.accept()
    threading.Thread(target=handle_client, args=(client,), daemon=True).start()

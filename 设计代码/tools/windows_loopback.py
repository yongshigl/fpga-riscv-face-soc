#!/usr/bin/env python3
"""Forward a WSL loopback port to Windows loopback without firewall changes."""
import argparse
import os
import socketserver
import subprocess
import threading


WINDOWS_RELAY = r"""
import msvcrt, os, socket, sys, threading
msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
connection = socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=10)
connection.settimeout(None)
def upstream():
    try:
        while True:
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                break
            connection.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass
threading.Thread(target=upstream, daemon=True).start()
try:
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            view = view[os.write(sys.stdout.fileno(), view):]
finally:
    connection.close()
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-port", type=int, required=True)
    parser.add_argument("--windows-port", type=int, required=True)
    parser.add_argument("--windows-python", default="/mnt/c/python.exe")
    args = parser.parse_args()

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            process = subprocess.Popen(
                [args.windows_python, "-u", "-c", WINDOWS_RELAY, str(args.windows_port)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

            def upstream():
                try:
                    while chunk := self.request.recv(65536):
                        process.stdin.write(chunk)
                        process.stdin.flush()
                except OSError:
                    pass
                finally:
                    process.stdin.close()

            thread = threading.Thread(target=upstream, daemon=True)
            thread.start()
            try:
                while chunk := os.read(process.stdout.fileno(), 65536):
                    self.request.sendall(chunk)
            except OSError:
                pass
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
                process.stdout.close()
                self.request.shutdown(2)
                thread.join(timeout=2)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", args.local_port), Relay) as server:
        print(f"WINDOWS_LOOPBACK_READY 127.0.0.1:{args.local_port} -> Windows:{args.windows_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

import socket

DEFAULT_BUFFER = 4096

class UDPSocket:
    def __init__(self, address, reuseaddr=True, timeout=0.02, buffer_bytes=DEFAULT_BUFFER):
        self.buffer_bytes = int(buffer_bytes)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if reuseaddr:
                try:
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                except Exception:
                    pass
            self.socket.bind(address)
            self.socket.settimeout(timeout)
        except Exception:
            try:
                self.socket.close()   
            finally:
                raise

    def close(self):
        try:
            self.socket.close()
        except Exception:
            pass

    # Optional safety net (in case someone forgets to close)
    def __del__(self):
        try:
            self.socket.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()
        return True

    def receive(self):
        try:
            msg, address = self.socket.recvfrom(self.buffer_bytes)
            return True, msg.decode('utf-8', errors='replace'), address
        except socket.timeout:
            return False, None, None
        except ConnectionResetError:
            # Windows-specific "ICMP port unreachable" noise for UDP
            return False, None, None


    def send(self, msg, address):
        self.socket.sendto(str(msg).encode('ascii', 'ignore'), address)

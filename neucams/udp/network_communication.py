import socket
import pickle
import time
from multiprocessing import Process, Event, Manager
from threading import Thread
from io import BlockingIOError

BUFFER_SIZE = 1024
class UDPSocket:
    """ https://wiki.python.org/moin/UdpCommunication
        connectionless protocol, simple and fast, but can fail to deliver the message
        used for short, non-critical messages
    """
    def __init__(self, address):
        # address = (ip, port)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(address)
        self.socket.settimeout(.02)
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.socket.close()
        return True

    def receive(self):
        try:
            msg, address = self.socket.recvfrom(BUFFER_SIZE)
            return True, msg.decode(), address
        except socket.timeout:
            return False, None, None

    def send(self, msg, address):
        # address = (ip, port)
        self.socket.sendto(msg.encode('ascii'),address)

class TCPServer(Process):
    """ https://wiki.python.org/moin/TcpCommunication
        connection oriented protocol, retries on failure, 'slow'
        used for object streaming, critical messages
    """
    def __init__(self, address, handler, handler_kwargs = {}):
        """Takes an address (such as ('localhost', 5004) or ('127.0.0.1', 5004))
        and an handler, a class inheriting Thread, QThread or Process, that will handle the socket.
        Optionally, you can pass a dictionary that will be used by the handler.
        This dictionary is used to create a served dictionary; every change to the served dictionary, handler_dict, will be visible to the handler instances.
        Following the principles in https://docs.python.org/3/howto/sockets.html, this server class does not send or receive messages, it creates client sockets and pass them to handler instances."""
        super().__init__()
        
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # so the port can be reused immediately after being closed
        self.server_socket.bind(address)
        self.server_socket.settimeout(2)
        self.server_socket.listen(25)
        
        self.handler = handler
        
        manager = Manager()
        self.handler_dict = manager.dict(**handler_kwargs)
        
        self.start_flag = Event()
        self.close_flag = Event()
        self.exit_flag = Event()

        self.start()
        self.start_flag.wait()
        
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.close()
    
    def close(self):
        self.close_flag.set()
        self.exit_flag.wait()
        self.join()
        # self.server_socket.shutdown(socket.SHUT_RDWR)
        self.server_socket.close()
        
    def run(self):
        self.start_flag.set()
        while not self.close_flag.is_set():
            try:
                (client_socket, address) = self.server_socket.accept()
                thread = self.handler(client_socket)
                thread.setup({**self.handler_dict})
                thread.start()
            except BlockingIOError:
                time.sleep(0.001)
            except socket.timeout:
                pass
            except Exception as e:
                print(e, flush=True)
            
        self.exit_flag.set()
        
class TCPHandler(Thread):
    def __init__(self, client_socket):
        super().__init__()
        self.client_socket = client_socket
        msg = self.client_socket.recv(BUFFER_SIZE)
        self.msg_received = msg.decode() if msg else None

    def run(self):
        self.handle()
        self.cleanup()
        
    def setup(self, manager_dict):
        """call after instantiating and before starting for custom setup"""
        pass # IMPLEMENT THIS IN CHILD (see test_tcp_protocol.py for examples)
    
    def handle(self):
        pass # IMPLEMENT THIS IN CHILD (see test_tcp_protocol.py for examples)
        
    def cleanup(self):
        self.client_socket.close()

def TCP_query(server_address, msg, verbose = False):
    """A TCP client socket can only be used for one query, after which the socket is destroyed.
    usage: data = TCP_query(('127.0.0.1', 5004),'ping')"""
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    client_socket.settimeout(0.1)
    data = None
    try:
        client_socket.connect(server_address)
        if verbose: print("sending ", msg, flush=True)
        client_socket.sendall(msg.encode('ascii'))
        while True:
            try:
                packet = client_socket.recv(BUFFER_SIZE)
            except socket.timeout:
                if verbose: print('Client timed out, consider making the socket blocking with settimeout(None)', flush=True)
                return None
            if not packet: break
            if data is None:
                data = []
            data.append(packet)
    except Exception as e:
        if verbose: print(e, flush=True)
    if data is not None:
        data = b"".join(data)
        try:
            data = pickle.loads(data)
        except pickle.UnpicklingError as e:
            data = data.decode()
    if verbose: print("received ", data, flush=True)
    # client_socket.shutdown(socket.SHUT_RDWR)
    client_socket.close()
    return data


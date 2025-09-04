import socket
import sys

# --- Configuration ---
# This should match the 'server_ip' and 'server_port' in your JSON config.
# '0.0.0.0' in the server means it listens on all interfaces;
# to connect from the same machine, we use '127.0.0.1'.
UDP_IP = "127.0.0.1"
UDP_PORT = 9999
# ---------------------

def send_udp_message(message: str):
    """
    Sends a single UDP message to the configured IP and port.
    """
    if not message:
        print("Error: No message provided.")
        return

    print(f"Sending UDP message to {UDP_IP}:{UDP_PORT}")
    print(f"Message: '{message}'")

    try:
        # Create a UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Send the message
        sock.sendto(message.encode('utf-8'), (UDP_IP, UDP_PORT))
        
        print("Message sent successfully.")

        # Listen for a response (optional, but good for confirmation)
        sock.settimeout(2.0) # Wait up to 2 seconds for a reply
        try:
            response, addr = sock.recvfrom(1024)
            print(f"Received response from {addr}: {response.decode('utf-8')}")
        except socket.timeout:
            print("No response received from the server.")
            
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        if 'sock' in locals() and sock:
            sock.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Join all arguments after the script name into a single message string
        message_to_send = " ".join(sys.argv[1:])
        send_udp_message(message_to_send)
    else:
        print("Usage: python send_udp.py <message>")
        print("Example: python send_udp.py my_experiment_run_1")

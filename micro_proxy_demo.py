"""
micro_proxy_demo.py - Minimal User-Space Micro-Proxy with IP_UNICAST_IF
----------------------------------------------------------------------
Demonstrates:
1. In-tunnel clean DNS query over UDP against VPN internal DNS.
2. Binding outbound TCP sockets to VPN adapter via IP_UNICAST_IF (option 31).
3. HTTP CONNECT tunneling for transparent HTTPS forwarding.
"""

import sys
import socket
import select
import struct
import threading
import argparse

IP_UNICAST_IF = 31  # Winsock socket option for IPPROTO_IP

def direct_dns_query(domain, dns_server, timeout=2.0):
    """
    Constructs a minimal DNS query packet and queries internal VPN DNS server directly.
    """
    import random
    query_id = random.randint(1, 65535)
    # Header: ID, Flags (standard query 0x0100), QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
    header = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    
    qname = b""
    for part in domain.split("."):
        qname += bytes([len(part)]) + part.encode("ascii")
    qname += b"\x00"
    
    # QTYPE=1 (A), QCLASS=1 (IN)
    question = qname + struct.pack("!HH", 1, 1)
    packet = header + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (dns_server, 53))
        data, _ = sock.recvfrom(1024)
        if len(data) < 12:
            return None
        
        # Parse answers
        ancount = struct.unpack("!H", data[6:8])[0]
        if ancount == 0:
            return None
            
        offset = 12
        # Skip question
        while data[offset] != 0:
            offset += data[offset] + 1
        offset += 5  # null byte + type + class
        
        for _ in range(ancount):
            if offset >= len(data):
                break
            # Skip name (handle compression pointer)
            if (data[offset] & 0xC0) == 0xC0:
                offset += 2
            else:
                while data[offset] != 0:
                    offset += data[offset] + 1
                offset += 1
            
            atype, aclass, ttl, rdlength = struct.unpack("!HHIH", data[offset:offset+10])
            offset += 10
            if atype == 1 and rdlength == 4:  # Type A
                ip = socket.inet_ntoa(data[offset:offset+4])
                return ip
            offset += rdlength
    except Exception as e:
        print(f"[DNS Warning] Failed to query {domain} from {dns_server}: {e}")
    finally:
        sock.close()
    return None

def handle_client(client_sock, vpn_ifindex, dns_server):
    try:
        client_sock.settimeout(15.0)
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = client_sock.recv(4096)
            if not chunk:
                client_sock.close()
                return
            req += chunk

        first_line = req.split(b"\r\n")[0].decode("latin1", errors="replace")
        parts = first_line.split()
        if len(parts) < 2:
            client_sock.close()
            return
            
        method, target = parts[0], parts[1]

        if method == "CONNECT":
            # Target is host:port
            host, port_str = target.split(":") if ":" in target else (target, "443")
            port = int(port_str)
            
            # 1. Resolve host using VPN internal DNS
            resolved_ip = direct_dns_query(host, dns_server) if dns_server else None
            dest_host = resolved_ip if resolved_ip else host

            # 2. Create outbound socket bound to VPN interface
            remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if vpn_ifindex:
                # Set IP_UNICAST_IF
                remote_sock.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, struct.pack("!I", vpn_ifindex))
            
            remote_sock.settimeout(10.0)
            remote_sock.connect((dest_host, port))
            
            # Send 200 Connection Established to client
            client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            
            # Pipe bidirectional traffic
            sockets = [client_sock, remote_sock]
            while True:
                r, _, _ = select.select(sockets, [], [], 60.0)
                if not r:
                    break
                for s in r:
                    other = remote_sock if s is client_sock else client_sock
                    data = s.recv(16384)
                    if not data:
                        return
                    other.sendall(data)
    except Exception as e:
        pass
    finally:
        client_sock.close()

def main():
    parser = argparse.ArgumentParser(description="Micro-Proxy bound to VPN interface via IP_UNICAST_IF.")
    parser.add_argument("--port", type=int, default=10811, help="Local listening port (default: 10811)")
    parser.add_argument("--vpn-index", type=int, required=True, help="Interface Index of the VPN adapter")
    parser.add_argument("--dns-server", default="10.240.254.254", help="VPN internal DNS server IP for resolving")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(128)
    print(f"[+] Micro-Proxy listening on 127.0.0.1:{args.port}")
    print(f"[+] Outbound bound to Interface Index: {args.vpn_index}, Internal DNS: {args.dns_server}")

    try:
        while True:
            client, _ = server.accept()
            t = threading.Thread(target=handle_client, args=(client, args.vpn_index, args.dns_server), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[*] Shutting down proxy.")
    finally:
        server.close()

if __name__ == "__main__":
    main()

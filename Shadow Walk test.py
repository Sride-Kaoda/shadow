import socket
import threading
import time
import queue
import collections
from flask import Flask
import tkinter as tk
from tkinter import font
import requests
from pynput import keyboard

DELAY_ENABLED = False
DELAY_MS = 1200
DELAY_EVENT = threading.Event()  # signals when delay is toggled OFF
STOP_EVENT = threading.Event()  # signals to stop the application

# Rate limiting
RATE_LIMIT_ENABLED = False
INBOUND_DELAY_MS = 10000  # Delay each inbound packet by this many ms (5 seconds)
RATE_LIMIT_LOCK = threading.Lock()

# Teleport effect (limit outbound to 1 bps while inbound flows)
TELEPORT_ENABLED = False
TELEPORT_BPS = 1  # Bytes per second for outbound

app = Flask(__name__)

# Overlay window
overlay_window = None
overlay_label = None

def create_overlay():
    global overlay_window, overlay_label
    
    overlay_window = tk.Tk()
    overlay_window.attributes('-topmost', True)  # Always on top
    overlay_window.attributes('-alpha', 0.85)    # Semi-transparent
    overlay_window.overrideredirect(True)        # No window decoration
    overlay_window.resizable(False, False)
    
    # Create main frame
    main_frame = tk.Frame(overlay_window, bg='black')
    main_frame.pack()
    
    overlay_label = tk.Label(
        main_frame,
        text="Delay: OFF | Freeze: OFF | Teleport: OFF",
        font=font.Font(family='Arial', size=16, weight='bold'),
        bg='black',
        fg='lime',
        padx=20,
        pady=15
    )
    overlay_label.pack()
    
    # Stop button
    stop_button = tk.Button(
        main_frame,
        text="STOP",
        font=font.Font(family='Arial', size=10, weight='bold'),
        bg='red',
        fg='white',
        padx=10,
        pady=5,
        command=stop_application,
        border=0,
        activebackground='darkred'
    )
    stop_button.pack(pady=(0, 10))
    
    # Get screen width and position in top-right
    overlay_window.update_idletasks()
    screen_width = overlay_window.winfo_screenwidth()
    window_width = overlay_label.winfo_reqwidth() + 40
    
    # Position at top-right with 10px margin
    x_pos = max(0, screen_width - window_width - 10)
    overlay_window.geometry(f'+{x_pos}+20')
    
    def update_loop():
        try:
            update_overlay()
            overlay_window.after(500, update_loop)  # Update every 500ms
        except:
            pass
    
    overlay_window.after(100, update_loop)

def update_overlay():
    global overlay_label
    if overlay_label:
        delay_status = "Delay: ON" if DELAY_ENABLED else "Delay: OFF"
        freeze_status = "Freeze: ON" if RATE_LIMIT_ENABLED else "Freeze: OFF"
        teleport_status = "Teleport: ON" if TELEPORT_ENABLED else "Teleport: OFF"
        fg_color = 'red' if (DELAY_ENABLED or RATE_LIMIT_ENABLED or TELEPORT_ENABLED) else 'lime'
        overlay_label.config(text=f"{delay_status} | {freeze_status} | {teleport_status}", fg=fg_color)

def stop_application():
    global overlay_window, STOP_EVENT
    STOP_EVENT.set()
    try:
        overlay_window.destroy()
    except:
        pass
    import os
    os._exit(0)  # Force exit

def setup_hotkey():
    """Set up ']' key to toggle delay, '[' key to toggle freeze, and ';' key to toggle teleport"""
    last_press = {"time": 0, "time_bracket": 0, "time_semicolon": 0}  # Track last key press time
    
    def on_press(key):
        try:
            # Support character key ']' from KeyCode
            if (hasattr(key, 'char') and key.char == ']') or key == keyboard.KeyCode.from_char(']'):
                # Debounce: only allow toggle if 0.3 seconds have passed since last press
                current_time = time.time()
                if current_time - last_press["time"] > 0.3:
                    last_press["time"] = current_time
                    # Send request to toggle endpoint
                    try:
                        requests.get('http://127.0.0.1:5000/toggle', timeout=1)
                    except:
                        pass
            # Support '[' key for freeze
            elif (hasattr(key, 'char') and key.char == '[') or key == keyboard.KeyCode.from_char('['):
                current_time = time.time()
                if current_time - last_press["time_bracket"] > 0.3:
                    last_press["time_bracket"] = current_time
                    try:
                        requests.get('http://127.0.0.1:5000/toggle_rate', timeout=1)
                    except:
                        pass
            # Support ';' key for teleport
            elif (hasattr(key, 'char') and key.char == ';') or key == keyboard.KeyCode.from_char(';'):
                current_time = time.time()
                if current_time - last_press["time_semicolon"] > 0.3:
                    last_press["time_semicolon"] = current_time
                    try:
                        requests.get('http://127.0.0.1:5000/toggle_teleport', timeout=1)
                    except:
                        pass
        except AttributeError:
            pass
    
    # Start hotkey listener in daemon thread
    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()

@app.route("/toggle")
def toggle():
    global DELAY_ENABLED
    DELAY_ENABLED = not DELAY_ENABLED
    if not DELAY_ENABLED:
        DELAY_EVENT.set()
    else:
        DELAY_EVENT.clear()
    status = "Delay ON" if DELAY_ENABLED else "Delay OFF"
    print(status)
    update_overlay()
    return status

@app.route("/toggle_rate")
def toggle_rate():
    global RATE_LIMIT_ENABLED
    RATE_LIMIT_ENABLED = not RATE_LIMIT_ENABLED
    status = "Freeze ON" if RATE_LIMIT_ENABLED else "Freeze OFF"
    print(status)
    update_overlay()
    return status

@app.route("/delay/<int:ms>")
def set_delay(ms):
    global INBOUND_DELAY_MS
    if ms > 0:
        INBOUND_DELAY_MS = ms
        return f"Inbound delay set to {ms}ms"
    return "Invalid delay. Must be > 0"

@app.route("/toggle_teleport")
def toggle_teleport():
    global TELEPORT_ENABLED
    TELEPORT_ENABLED = not TELEPORT_ENABLED
    status = "Teleport ON" if TELEPORT_ENABLED else "Teleport OFF"
    print(status)
    update_overlay()
    return status

@app.route("/teleport_bps/<int:bps>")
def set_teleport_bps(bps):
    global TELEPORT_BPS
    if bps > 0:
        TELEPORT_BPS = bps
        return f"Teleport BPS set to {bps}"
    return "Invalid BPS. Must be > 0"


def handle_client(client_socket):
    try:
        # SOCKS5 handshake
        header = client_socket.recv(2)
        if len(header) < 2:
            client_socket.close()
            return

        version, nmethods = header
        methods = client_socket.recv(nmethods)
        client_socket.sendall(b"\x05\x00")  # version 5, no auth

        # SOCKS5 connection request
        req = client_socket.recv(4)
        if len(req) < 4:
            client_socket.close()
            return

        ver, cmd, _, atyp = req

        if atyp == 1:  # IPv4
            addr = socket.inet_ntoa(client_socket.recv(4))
        elif atyp == 3:  # Domain name
            length = client_socket.recv(1)[0]
            addr = client_socket.recv(length).decode()
        else:
            client_socket.close()
            return

        port = int.from_bytes(client_socket.recv(2), "big")

        # Connect to target server
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.connect((addr, port))

        # SOCKS5 success reply
        reply = b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + (0).to_bytes(2, "big")
        client_socket.sendall(reply)

        # Queue for outbound packets
        outbound_queue = queue.Queue()
        stop_flag = {"stop": False}

        # Reader: client → queue (timestamp packets)
        def outbound_reader():
            try:
                while True:
                    data = client_socket.recv(4096)
                    if not data:
                        break

                    arrival = time.time()
                    outbound_queue.put((arrival, data))

            except:
                pass
            finally:
                stop_flag["stop"] = True
                outbound_queue.put(None)
                try: remote.close()
                except: pass
                try: client_socket.close()
                except: pass

        # Scheduler: hold packets while DELAY_ENABLED, release on toggle or after delay expires
        def outbound_sender():
            try:
                # Rate limiting for teleport
                bytes_this_second = 0
                second_start = time.time()
                
                while True:
                    item = outbound_queue.get()
                    if item is None:
                        break

                    arrival_time, data = item

                    # If delay is ON → hold this packet until either:
                    # 1. DELAY_MS pass, OR
                    # 2. Delay is toggled OFF
                    if DELAY_ENABLED:
                        release_time = arrival_time + (DELAY_MS / 1000.0)
                        while DELAY_ENABLED and time.time() < release_time:
                            remaining = release_time - time.time()
                            if remaining <= 0:
                                break
                            # Wait up to remaining time, but wake if toggled OFF
                            DELAY_EVENT.wait(timeout=min(remaining, 0.01))

                    # Apply teleport rate limiting (limit outbound to N bytes/sec)
                    if TELEPORT_ENABLED:
                        current_time = time.time()
                        # Reset counter each second
                        if current_time - second_start >= 1.0:
                            bytes_this_second = 0
                            second_start = current_time
                        
                        # Check if we can send this packet
                        if bytes_this_second + len(data) > TELEPORT_BPS:
                            # Need to wait until next second window
                            wait_time = 1.0 - (current_time - second_start)
                            if wait_time > 0:
                                time.sleep(wait_time)
                            bytes_this_second = 0
                            second_start = time.time()
                        
                        bytes_this_second += len(data)

                    # Send packet (either delay expired naturally or was toggled OFF)
                    try:
                        remote.sendall(data)
                    except:
                        break

            finally:
                stop_flag["stop"] = True
                try: remote.close()
                except: pass
                try: client_socket.close()
                except: pass

        # Inbound (server → client) with freeze capability (delay instead of pause)
        def inbound():
            try:
                while True:
                    data = remote.recv(4096)
                    if not data:
                        break
                    
                    # If freeze enabled, delay this packet significantly
                    if RATE_LIMIT_ENABLED:
                        time.sleep(INBOUND_DELAY_MS / 1000.0)
                    
                    client_socket.sendall(data)
            except:
                pass
            finally:
                stop_flag["stop"] = True
                try: remote.close()
                except: pass
                try: client_socket.close()
                except: pass

        threading.Thread(target=outbound_reader, daemon=True).start()
        threading.Thread(target=outbound_sender, daemon=True).start()
        threading.Thread(target=inbound, daemon=True).start()

    except Exception:
        try: client_socket.close()
        except: pass

def start_socks5(port=8888):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(100)
    print(f"SOCKS5 proxy listening on 127.0.0.1:{port}")

    while True:
        client_socket, _ = server.accept()
        threading.Thread(target=handle_client, args=(client_socket,), daemon=True).start()

def start_flask():
    app.run(port=5000, threaded=True, use_reloader=False, debug=False)

if __name__ == "__main__":
    threading.Thread(target=start_socks5, daemon=True).start()
    threading.Thread(target=start_flask, daemon=True).start()
    
    # Set up hotkey listener
    setup_hotkey()
    
    # Start overlay in main thread
    create_overlay()
    
    # Keep main thread alive
    try:
        overlay_window.mainloop()
    except KeyboardInterrupt:
        pass

import threading
import time
# pyrefly: ignore [missing-import]
import webview
import sys
import os
from pos_backend import app, init_db

def start_server():
    # Use a specific port to avoid conflicts, disable reloader since we are running in a thread
    app.run(host='127.0.0.1', port=5000, use_reloader=False)

if __name__ == '__main__':
    # Initialize Database (products are imported via web upload on first launch)
    init_db()
    
    # Start Flask Server in a daemon thread
    t = threading.Thread(target=start_server)
    t.daemon = True
    t.start()
    
    # Give the server a moment to start
    time.sleep(1)
    
    # Create the native desktop window via PyWebView
    webview.create_window(
        title='POS NXT Solutions', 
        url='http://127.0.0.1:5000',
        width=1200, 
        height=800,
        min_size=(1024, 768)
    )
    webview.start()
    
    # Exit gracefully when window is closed
    sys.exit()

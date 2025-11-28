#!/usr/bin/env python3
"""
Simple HTTP server to view LCD simulator frames in a browser.

Serves the latest.png frame with auto-refresh for live viewing.
Run this alongside lcd_simulator.py to watch the display remotely.

Usage:
    python dev/lcd_viewer_server.py
    
Then open http://localhost:8765 in your browser.
"""

import http.server
import socketserver
from pathlib import Path

PORT = 8765
FRAME_DIR = Path(__file__).parent / "lcd_frames"

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>LCD Simulator Viewer</title>
    <meta http-equiv="refresh" content="1">
    <style>
        body {
            background: #1a1a2e;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            font-family: monospace;
            color: #00c8ff;
        }
        h1 {
            margin-bottom: 20px;
        }
        .frame-container {
            background: #000;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 0 30px rgba(0, 200, 255, 0.3);
        }
        img {
            display: block;
            image-rendering: pixelated;
            border: 2px solid #00c8ff;
        }
        .info {
            margin-top: 20px;
            color: #666;
        }
        .no-image {
            padding: 40px;
            color: #ff6b6b;
        }
    </style>
</head>
<body>
    <h1>🖥️ LCD Simulator</h1>
    <div class="frame-container">
        {content}
    </div>
    <div class="info">Auto-refreshes every second</div>
</body>
</html>
"""


class LCDViewerHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRAME_DIR), **kwargs)
    
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            latest = FRAME_DIR / "latest.png"
            if latest.exists():
                content = '<img src="/latest.png" alt="LCD Frame" width="640" height="320">'
            else:
                content = '<div class="no-image">No frames yet. Run lcd_simulator.py to generate frames.</div>'
            
            html = HTML_TEMPLATE.format(content=content)
            self.wfile.write(html.encode())
        else:
            super().do_GET()
    
    def log_message(self, format, *args):
        # Suppress access logs for cleaner output
        pass


def main():
    FRAME_DIR.mkdir(exist_ok=True)
    
    with socketserver.TCPServer(("", PORT), LCDViewerHandler) as httpd:
        print(f"LCD Viewer Server running at http://localhost:{PORT}")
        print(f"Serving frames from: {FRAME_DIR}")
        print("Press Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")


if __name__ == "__main__":
    main()

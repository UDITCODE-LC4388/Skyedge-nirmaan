#!/usr/bin/env python3
"""
SkyEdge Ground Station Viewer
-----------------------------
A Windows/Cross-platform desktop application built with Tkinter for:
- Wirelessly connecting to the SkyEdge Ground Station (ESP32 #2) via HTTP/REST.
- Live handshake and connection verification (no hardcoded/fake connected state).
- Receiving and parsing real-time edge images and AI/ML justification metadata.
- Maintaining a scrollable history of received detections with inspection support.
- Audit logging of all edge transmission decisions with timestamps and rationales.
- True wireless telemetry link with automatic disconnection detection.
"""

import base64
import io
import json
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime, timezone
from tkinter import messagebox, ttk
import urllib.request
import urllib.error

# Optional Pillow import for image rendering
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ==============================================================================
# Data Structures
# ==============================================================================

@dataclass
class DetectionRecord:
    record_id: int
    received_at: str
    event_type: str = "UNKNOWN"
    confidence: float = 0.0
    severity: float = 0.0
    urgency: float = 0.0
    mission_value: float = 0.0
    final_score: float = 0.0
    action: str = "TRANSMIT_NOW"
    justification: str = ""
    iso_timestamp: str = ""
    raw_image_bytes: bytes = field(default=b"", repr=False)
    image_size: tuple[int, int] = (0, 0)
    chunk_count: int = 0
    status: str = "COMPLETE"


# ==============================================================================
# Wireless Ground Station Communication Worker
# ==============================================================================

class WirelessGroundStationThread(threading.Thread):
    """
    Background worker that connects wirelessly to the ESP32 Ground Station over HTTP.
    Performs real handshakes and continuous polling:
      - /api/status: Link heartbeat, client count, free heap
      - /api/images: Strictly ordered list of reassembled aerial detections
      - /image/<id>: Raw binary JPEG download
    """

    def __init__(
        self,
        host: str,
        port: int,
        event_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        super().__init__(daemon=True)
        self.host = host.strip()
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"
        self.queue = event_queue
        self.stop_event = stop_event
        self.fetched_image_ids = set()

    def run(self):
        # 1. Real Handshake: Test if ESP32 Ground Station is actually reachable wirelessly
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/status",
                headers={"User-Agent": "SkyEdge-Ground-Viewer/2.0"},
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                if resp.status == 200:
                    raw_data = resp.read().decode("utf-8")
                    data = json.loads(raw_data)
                    self.queue.put(
                        ("CONNECTED", f"Wireless link established: {self.base_url}")
                    )
                    self.queue.put(("STATUS_UPDATE", data))
                else:
                    self.queue.put(
                        ("ERROR", f"ESP32 returned HTTP status {resp.status}")
                    )
                    return
        except urllib.error.URLError as e:
            err_msg = (
                f"Cannot reach ESP32 at {self.base_url}. "
                f"Ensure your laptop Wi-Fi is connected to 'SkyEdge-Ground'. ({e.reason})"
            )
            self.queue.put(("ERROR", err_msg))
            return
        except Exception as e:
            err_msg = f"Wireless connection failed to {self.base_url}: {e}"
            self.queue.put(("ERROR", err_msg))
            return

        # 2. Continuous Polling Loop
        consecutive_errors = 0
        while not self.stop_event.is_set():
            try:
                # A. Poll Status Endpoint
                req_status = urllib.request.Request(
                    f"{self.base_url}/api/status",
                    headers={"User-Agent": "SkyEdge-Ground-Viewer/2.0"},
                )
                with urllib.request.urlopen(req_status, timeout=2.0) as resp:
                    if resp.status == 200:
                        status_data = json.loads(resp.read().decode("utf-8"))
                        self.queue.put(("STATUS_UPDATE", status_data))
                        consecutive_errors = 0

                # B. Poll Images Endpoint
                req_images = urllib.request.Request(
                    f"{self.base_url}/api/images",
                    headers={"User-Agent": "SkyEdge-Ground-Viewer/2.0"},
                )
                with urllib.request.urlopen(req_images, timeout=2.5) as resp:
                    if resp.status == 200:
                        images_data = json.loads(resp.read().decode("utf-8"))
                        images_list = images_data.get("images", [])

                        # Sort ascending by image_id to guarantee mission sequence order
                        sorted_imgs = sorted(
                            images_list, key=lambda x: int(x.get("image_id", 0))
                        )

                        for img_info in sorted_imgs:
                            img_id = int(img_info.get("image_id", 0))
                            if img_id not in self.fetched_image_ids:
                                # C. Download Binary JPEG Image
                                img_url = f"{self.base_url}/image/{img_id}"
                                try:
                                    req_img = urllib.request.Request(
                                        img_url,
                                        headers={
                                            "User-Agent": "SkyEdge-Ground-Viewer/2.0"
                                        },
                                    )
                                    with urllib.request.urlopen(
                                        req_img, timeout=3.5
                                    ) as img_resp:
                                        if img_resp.status == 200:
                                            jpeg_bytes = img_resp.read()
                                            self.fetched_image_ids.add(img_id)
                                            self.queue.put(
                                                (
                                                    "NEW_DETECTION",
                                                    (jpeg_bytes, img_info),
                                                )
                                            )
                                except Exception as err:
                                    self.queue.put(
                                        (
                                            "LOG",
                                            f"Failed to download image #{img_id}: {err}",
                                        )
                                    )

            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    self.queue.put(
                        (
                            "ERROR",
                            f"Wireless link dropped: No response from {self.base_url} ({e})",
                        )
                    )
                    break

            # Pacing delay between poll cycles
            for _ in range(15):
                if self.stop_event.is_set():
                    break
                time.sleep(0.1)

        self.queue.put(("DISCONNECTED", f"Disconnected from {self.base_url}"))


# ==============================================================================
# Main GUI Application
# ==============================================================================

class SkyEdgeViewerApp(tk.Tk):
    """Tkinter Ground Station Viewer Application"""

    # Visual Theme Palette (Modern Deep Space)
    COLOR_BG = "#0f172a"          # Slate 900
    COLOR_CARD = "#1e293b"        # Slate 800
    COLOR_CARD_BORDER = "#334155" # Slate 700
    COLOR_FG = "#cbd5e1"          # Slate 300
    COLOR_FG_BRIGHT = "#f8fafc"   # Slate 50
    COLOR_ACCENT = "#38bdf8"      # Sky Blue 400
    COLOR_GREEN = "#10b981"       # Emerald 500
    COLOR_RED = "#ef4444"         # Red 500
    COLOR_ORANGE = "#f59e0b"      # Amber 500
    COLOR_PURPLE = "#8b5cf6"      # Violet 500
    COLOR_YELLOW = "#eab308"      # Yellow 500
    COLOR_MUTED = "#64748b"       # Slate 500

    def __init__(self):
        super().__init__()
        self.title("SkyEdge — Autonomous Ground Station Wireless Viewer")
        self.geometry("1240x840")
        self.minsize(1080, 720)
        self.configure(bg=self.COLOR_BG)

        # Threading and Communication State
        self.event_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.client_thread: WirelessGroundStationThread | None = None

        # Application Data State
        self.detections: list[DetectionRecord] = []
        self.detection_counter: int = 0
        self.active_detection: DetectionRecord | None = None
        self.current_pil_image: Image.Image | None = None
        self.current_photo_image: ImageTk.PhotoImage | None = None
        self.last_packet_time: float = 0.0
        self.is_connected: bool = False

        # Build UI Components
        self._apply_styles()
        self._build_header_toolbar()
        self._build_main_workspace()
        self._build_bottom_console()
        self._build_toast_banner()

        # Start periodic queue polling & timeout watchdog
        self.after(50, self._poll_event_queue)
        self.after(1000, self._update_last_packet_counter)

        # Handle clean application exit
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Log system readiness
        self._log(
            "SkyEdge Wireless Viewer Initialized. Connect PC to Wi-Fi 'SkyEdge-Ground' and click Connect Wireless."
        )
        if not PIL_AVAILABLE:
            self._log(
                "WARNING: 'Pillow' is not installed. Run: pip install Pillow",
                level="WARN",
            )

    # --------------------------------------------------------------------------
    # Styling
    # --------------------------------------------------------------------------

    def _apply_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background=self.COLOR_BG, foreground=self.COLOR_FG)
        style.configure("TFrame", background=self.COLOR_BG, borderwidth=0, relief="flat")
        style.configure("Card.TFrame", background=self.COLOR_CARD, relief="solid", borderwidth=1)
        style.configure("TLabel", background=self.COLOR_BG, foreground=self.COLOR_FG, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=self.COLOR_CARD, foreground=self.COLOR_FG)
        style.configure("Header.TLabel", background=self.COLOR_CARD, foreground=self.COLOR_FG_BRIGHT, font=("Segoe UI", 11, "bold"))
        style.configure("Title.TLabel", background=self.COLOR_BG, foreground=self.COLOR_ACCENT, font=("Segoe UI", 13, "bold"))

        # Buttons
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=(10, 4), background=self.COLOR_CARD, foreground=self.COLOR_FG_BRIGHT, relief="flat")
        style.map("TButton", background=[("active", self.COLOR_CARD_BORDER), ("pressed", self.COLOR_ACCENT)], foreground=[("active", self.COLOR_FG_BRIGHT)])

        style.configure("Connect.TButton", background="#059669", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Connect.TButton", background=[("active", "#10b981"), ("pressed", "#047857")])

        style.configure("Disconnect.TButton", background="#dc2626", foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Disconnect.TButton", background=[("active", "#ef4444"), ("pressed", "#b91c1c")])

        # Notebook & Tabs Styling (High contrast, crystal-clear active tab)
        style.configure("TNotebook", background=self.COLOR_BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background="#1e293b",
            foreground="#94a3b8",
            font=("Segoe UI", 10, "bold"),
            padding=(18, 7),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#0284c7"), ("active", "#334155")],
            foreground=[("selected", "#ffffff"), ("active", "#f8fafc")],
        )

        # Treeview (History & Audit Log)
        style.configure(
            "Treeview",
            background="#0f172a",
            foreground="#f8fafc",
            fieldbackground="#0f172a",
            font=("Segoe UI", 10),
            rowheight=28,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#1e293b",
            foreground="#38bdf8",
            font=("Segoe UI", 10, "bold"),
            padding=(6, 6),
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#334155")],
            foreground=[("active", "#ffffff")],
        )
        style.map(
            "Treeview",
            background=[("selected", "#0284c7")],
            foreground=[("selected", "#ffffff")],
        )

        style.configure("Horizontal.TProgressbar", troughcolor=self.COLOR_CARD, background=self.COLOR_ACCENT, borderwidth=0, thickness=6)

    # --------------------------------------------------------------------------
    # UI Building Blocks
    # --------------------------------------------------------------------------

    def _build_header_toolbar(self):
        """Top Connection and Control Bar with Wireless Ground Link"""
        bar = tk.Frame(self, bg=self.COLOR_BG, height=52)
        bar.pack(side=tk.TOP, fill=tk.X, padx=14, pady=10)

        # Brand / App Title
        title_box = tk.Frame(bar, bg=self.COLOR_BG)
        title_box.pack(side=tk.LEFT)
        brand = tk.Label(title_box, text="SKYEDGE", font=("Segoe UI", 16, "bold"), fg=self.COLOR_ACCENT, bg=self.COLOR_BG)
        brand.pack(side=tk.LEFT)
        subbrand = tk.Label(title_box, text=" | Wireless Ground Station", font=("Segoe UI", 11), fg=self.COLOR_MUTED, bg=self.COLOR_BG)
        subbrand.pack(side=tk.LEFT)

        # Wireless Connection Controls Strip
        ctrl_strip = tk.Frame(bar, bg=self.COLOR_CARD, highlightthickness=1, highlightbackground=self.COLOR_CARD_BORDER)
        ctrl_strip.pack(side=tk.LEFT, padx=(24, 0), fill=tk.Y)

        inner = tk.Frame(ctrl_strip, bg=self.COLOR_CARD)
        inner.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        # Host / IP Entry
        tk.Label(inner, text="Ground Station IP:", font=("Segoe UI", 9, "bold"), fg=self.COLOR_FG_BRIGHT, bg=self.COLOR_CARD).pack(side=tk.LEFT, padx=(0, 6))

        self.entry_host = tk.Entry(
            inner,
            width=15,
            font=("Consolas", 10),
            bg="#0b1120",
            fg=self.COLOR_FG_BRIGHT,
            insertbackground=self.COLOR_ACCENT,
            relief="solid",
            borderwidth=1,
            highlightbackground=self.COLOR_CARD_BORDER,
        )
        self.entry_host.insert(0, "192.168.4.1")
        self.entry_host.pack(side=tk.LEFT, padx=(0, 10))

        # Port Entry
        tk.Label(inner, text="Port:", font=("Segoe UI", 9), fg=self.COLOR_MUTED, bg=self.COLOR_CARD).pack(side=tk.LEFT, padx=(0, 4))
        self.entry_port = tk.Entry(
            inner,
            width=5,
            font=("Consolas", 10),
            bg="#0b1120",
            fg=self.COLOR_FG_BRIGHT,
            insertbackground=self.COLOR_ACCENT,
            relief="solid",
            borderwidth=1,
            highlightbackground=self.COLOR_CARD_BORDER,
        )
        self.entry_port.insert(0, "80")
        self.entry_port.pack(side=tk.LEFT, padx=(0, 14))

        # Connect / Disconnect Button
        self.btn_connect = ttk.Button(
            inner,
            text="Connect Wireless",
            style="Connect.TButton",
            command=self._toggle_connection,
        )
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 14))

        # Status Dot & Text
        self.lbl_status_dot = tk.Label(inner, text="●", font=("Segoe UI", 14), fg=self.COLOR_RED, bg=self.COLOR_CARD)
        self.lbl_status_dot.pack(side=tk.LEFT, padx=(0, 4))
        self.lbl_status_text = tk.Label(
            inner,
            text="Disconnected",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLOR_FG,
            bg=self.COLOR_CARD,
            width=22,
            anchor="w",
        )
        self.lbl_status_text.pack(side=tk.LEFT)

        # Last Ping / Packet Counter
        self.lbl_last_packet = tk.Label(
            inner,
            text="Last ping: Never",
            font=("Segoe UI", 8),
            fg=self.COLOR_MUTED,
            bg=self.COLOR_CARD,
            width=18,
            anchor="w",
        )
        self.lbl_last_packet.pack(side=tk.LEFT, padx=(6, 0))

        # Telemetry Pill on the Right
        info_box = tk.Frame(bar, bg=self.COLOR_BG)
        info_box.pack(side=tk.RIGHT, fill=tk.Y, pady=4)

        self.lbl_wifi_hint = tk.Label(
            info_box,
            text="Wi-Fi: SkyEdge-Ground (Open)",
            font=("Segoe UI", 9, "bold"),
            fg=self.COLOR_ACCENT,
            bg=self.COLOR_CARD,
            padx=10,
            pady=4,
            relief="solid",
            borderwidth=1,
        )
        self.lbl_wifi_hint.pack(side=tk.TOP, anchor="e")

        self.lbl_telemetry_meta = tk.Label(
            info_box,
            text="Clients: 0 | Heap: -- KB",
            font=("Segoe UI", 8),
            fg=self.COLOR_MUTED,
            bg=self.COLOR_BG,
        )
        self.lbl_telemetry_meta.pack(side=tk.TOP, anchor="e", pady=(2, 0))

    def _build_toast_banner(self):
        """Hidden banner for showing toasts"""
        self.toast_frame = tk.Frame(self, bg=self.COLOR_RED, highlightthickness=1, highlightbackground="#ff7875")
        self.lbl_toast = tk.Label(self.toast_frame, text="", font=("Segoe UI", 10, "bold"), fg="#ffffff", bg=self.COLOR_RED, padx=20, pady=8)
        self.lbl_toast.pack(fill=tk.BOTH, expand=True)

    def _show_toast(self, message: str, duration_ms=4000, color=None):
        if color is None:
            color = self.COLOR_RED
        self.toast_frame.configure(bg=color)
        self.lbl_toast.configure(text=message, bg=color)
        self.toast_frame.place(relx=0.5, y=60, anchor="n")
        self.after(duration_ms, self.toast_frame.place_forget)

    def _update_last_packet_counter(self):
        if self.is_connected and self.last_packet_time > 0:
            elapsed = time.time() - self.last_packet_time
            self.lbl_last_packet.configure(text=f"Last ping: {elapsed:.1f}s ago")
        elif not self.is_connected:
            self.lbl_last_packet.configure(text="Last ping: Never")
        self.after(1000, self._update_last_packet_counter)

    def _build_main_workspace(self):
        """Builds the 3-column workspace: History | Live Image | ML Justification"""
        main_container = tk.Frame(self, bg=self.COLOR_BG)
        main_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=14, pady=10)

        # Left Column: History Panel
        self._build_history_panel(main_container)

        # Center Column: Image Canvas
        self._build_image_panel(main_container)

        # Right Column: ML Justification View
        self._build_ml_panel(main_container)

    def _build_history_panel(self, parent):
        """Scrollable detection history"""
        history_frame = tk.Frame(parent, bg=self.COLOR_CARD, width=280)
        history_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        history_frame.pack_propagate(False)

        header = tk.Frame(history_frame, bg=self.COLOR_CARD_BORDER, height=36)
        header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(header, text="Detection History", font=("Segoe UI", 10, "bold"), fg=self.COLOR_FG_BRIGHT, bg=self.COLOR_CARD_BORDER).pack(side=tk.LEFT, padx=10, pady=8)

        btn_clear = tk.Button(header, text="Clear", font=("Segoe UI", 8), bg=self.COLOR_CARD, fg=self.COLOR_MUTED, relief="flat", command=self._clear_history)
        btn_clear.pack(side=tk.RIGHT, padx=8, pady=4)

        # Treeview for detections
        tree_container = tk.Frame(history_frame, bg=self.COLOR_CARD)
        tree_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        columns = ("id", "event", "score", "time")
        self.history_tree = ttk.Treeview(tree_container, columns=columns, show="headings", selectmode="browse")
        self.history_tree.heading("id", text="#")
        self.history_tree.heading("event", text="Event")
        self.history_tree.heading("score", text="Score")
        self.history_tree.heading("time", text="Time")

        self.history_tree.column("id", width=36, anchor="center")
        self.history_tree.column("event", width=92, anchor="w")
        self.history_tree.column("score", width=58, anchor="center")
        self.history_tree.column("time", width=70, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_container, orient=tk.VERTICAL, command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=tree_scroll.set)

        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.history_tree.bind("<<TreeviewSelect>>", self._on_history_select)

    def _build_image_panel(self, parent):
        """Displays the reassembled edge camera frame"""
        image_frame = tk.Frame(parent, bg=self.COLOR_CARD)
        image_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        header = tk.Frame(image_frame, bg=self.COLOR_CARD_BORDER, height=36)
        header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(header, text="Reassembled Edge Image Frame", font=("Segoe UI", 10, "bold"), fg=self.COLOR_FG_BRIGHT, bg=self.COLOR_CARD_BORDER).pack(side=tk.LEFT, padx=10, pady=8)

        self.lbl_image_meta = tk.Label(header, text="No image received", font=("Segoe UI", 8), fg=self.COLOR_MUTED, bg=self.COLOR_CARD_BORDER)
        self.lbl_image_meta.pack(side=tk.RIGHT, padx=10, pady=8)

        canvas_box = tk.Frame(image_frame, bg="#020617")
        canvas_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.image_canvas = tk.Canvas(canvas_box, bg="#020617", highlightthickness=0)
        self.image_canvas.pack(fill=tk.BOTH, expand=True)
        self.image_canvas.bind("<Configure>", self._on_canvas_resize)

        self._draw_placeholder("Awaiting real-time wireless transmission from SkyEdge...")

    def _draw_placeholder(self, message: str):
        """Draws informational placeholder in the image canvas"""
        self.image_canvas.delete("all")
        cw = self.image_canvas.winfo_width()
        ch = self.image_canvas.winfo_height()
        if cw < 50:
            cw = 500
        if ch < 50:
            ch = 400

        self.image_canvas.create_text(
            cw // 2,
            ch // 2,
            text=message,
            fill=self.COLOR_MUTED,
            font=("Segoe UI", 11),
            justify="center",
        )

    def _build_ml_panel(self, parent):
        """ML Justification and Decision Information Panel"""
        ml_frame = tk.Frame(parent, bg=self.COLOR_CARD, width=340)
        ml_frame.pack(side=tk.LEFT, fill=tk.Y)
        ml_frame.pack_propagate(False)

        header = tk.Frame(ml_frame, bg=self.COLOR_CARD_BORDER, height=36)
        header.pack(side=tk.TOP, fill=tk.X)
        tk.Label(header, text="ML Justification & Decision", font=("Segoe UI", 10, "bold"), fg=self.COLOR_FG_BRIGHT, bg=self.COLOR_CARD_BORDER).pack(side=tk.LEFT, padx=10, pady=8)

        content = tk.Frame(ml_frame, bg=self.COLOR_CARD)
        content.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # 1. Event Classification Badge Card
        card_event = tk.Frame(content, bg="#282c34", highlightthickness=1, highlightbackground="#3e4451")
        card_event.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card_event, text="DETECTED EVENT TYPE", font=("Segoe UI", 8, "bold"), fg=self.COLOR_MUTED, bg="#282c34").pack(anchor="w", padx=10, pady=(8, 2))
        self.lbl_event_badge = tk.Label(card_event, text="NONE", font=("Segoe UI", 15, "bold"), fg=self.COLOR_MUTED, bg="#282c34")
        self.lbl_event_badge.pack(anchor="w", padx=10, pady=(0, 8))

        # 2. Priority & Arbitrated Action Card
        card_action = tk.Frame(content, bg="#282c34", highlightthickness=1, highlightbackground="#3e4451")
        card_action.pack(fill=tk.X, pady=(0, 12))

        tk.Label(card_action, text="ARBITRATED ACTION", font=("Segoe UI", 8, "bold"), fg=self.COLOR_MUTED, bg="#282c34").pack(anchor="w", padx=10, pady=(8, 2))
        self.lbl_action_badge = tk.Label(card_action, text="IDLE", font=("Segoe UI", 13, "bold"), fg=self.COLOR_FG, bg="#282c34")
        self.lbl_action_badge.pack(anchor="w", padx=10, pady=(0, 8))

        # 3. Decision Metrics Table
        metrics_frame = tk.Frame(content, bg=self.COLOR_CARD)
        metrics_frame.pack(fill=tk.X, pady=(0, 10))

        self.metric_widgets = {}
        metrics = [
            ("Priority Score", "lbl_final_score", self.COLOR_YELLOW),
            ("Confidence", "lbl_confidence", self.COLOR_ACCENT),
            ("Severity", "lbl_severity", self.COLOR_ORANGE),
            ("Urgency", "lbl_urgency", self.COLOR_PURPLE),
            ("Mission Value", "lbl_mission_value", self.COLOR_GREEN),
        ]

        for label_text, key, color in metrics:
            row = tk.Frame(metrics_frame, bg=self.COLOR_CARD)
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=label_text, font=("Segoe UI", 9), fg=self.COLOR_FG, bg=self.COLOR_CARD).pack(side=tk.LEFT)
            val_label = tk.Label(row, text="--", font=("Consolas", 10, "bold"), fg=color, bg=self.COLOR_CARD)
            val_label.pack(side=tk.RIGHT)
            self.metric_widgets[key] = val_label

        # Divider
        tk.Frame(content, bg=self.COLOR_CARD_BORDER, height=1).pack(fill=tk.X, pady=8)

        # 4. AI Explainability Callout Box
        just_card = tk.Frame(content, bg="#0b1120", highlightthickness=1, highlightbackground=self.COLOR_CARD_BORDER)
        just_card.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        tk.Label(just_card, text="AI EXPLAINABILITY & AUDIT RATIONALE", font=("Segoe UI", 8, "bold"), fg=self.COLOR_ACCENT, bg="#0b1120").pack(anchor="w", padx=10, pady=(8, 4))

        self.txt_justification = tk.Text(
            just_card,
            bg="#0b1120",
            fg="#e2e8f0",
            font=("Consolas", 9),
            wrap=tk.WORD,
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=4,
        )
        self.txt_justification.pack(fill=tk.BOTH, expand=True)
        self.txt_justification.insert(tk.END, "Awaiting transmission...")
        self.txt_justification.configure(state="disabled")

        # 5. Transmission Timing Metadata
        ts_frame = tk.Frame(content, bg=self.COLOR_CARD)
        ts_frame.pack(fill=tk.X)
        self.lbl_rx_time = tk.Label(ts_frame, text="Received: --", font=("Consolas", 8), fg=self.COLOR_MUTED, bg=self.COLOR_CARD)
        self.lbl_rx_time.pack(anchor="w")

    def _build_bottom_console(self):
        """Audit Log and Ground Station Console at the bottom"""
        console_frame = tk.Frame(self, bg=self.COLOR_CARD, height=195)
        console_frame.pack(side=tk.BOTTOM, fill=tk.X, expand=False, padx=14, pady=(0, 10))
        console_frame.pack_propagate(False)

        notebook = ttk.Notebook(console_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Audit Log
        audit_tab = tk.Frame(notebook, bg=self.COLOR_CARD)
        notebook.add(audit_tab, text="  Audit Log (AI Justifications)  ")

        audit_columns = ("time", "seq", "event", "score", "justification")
        self.audit_tree = ttk.Treeview(audit_tab, columns=audit_columns, show="headings", selectmode="browse")
        self.audit_tree.heading("time", text="Time")
        self.audit_tree.heading("seq", text="Seq #")
        self.audit_tree.heading("event", text="Hazard")
        self.audit_tree.heading("score", text="Score")
        self.audit_tree.heading("justification", text="AI Decision Rationale & Justification")

        self.audit_tree.column("time", width=110, anchor="center")
        self.audit_tree.column("seq", width=65, anchor="center")
        self.audit_tree.column("event", width=95, anchor="center")
        self.audit_tree.column("score", width=75, anchor="center")
        self.audit_tree.column("justification", width=720, anchor="w")

        # Row zebra striping and high-contrast text tags
        self.audit_tree.tag_configure("even", background="#0f172a", foreground="#f8fafc")
        self.audit_tree.tag_configure("odd", background="#182234", foreground="#f8fafc")
        self.audit_tree.tag_configure("info_row", background="#0f172a", foreground="#94a3b8", font=("Segoe UI", 9, "italic"))

        # Informative standby entry so table is never an empty black box
        self.audit_tree.insert(
            "",
            0,
            values=(
                "--:--:--",
                "#000",
                "STANDBY",
                "--",
                "Awaiting incoming telemetry... Live decision justifications will appear here in real-time."
            ),
            tags=("info_row",),
        )

        audit_scroll = ttk.Scrollbar(audit_tab, orient=tk.VERTICAL, command=self.audit_tree.yview)
        self.audit_tree.configure(yscrollcommand=audit_scroll.set)
        self.audit_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        audit_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Tab 2: Ground Link Console
        log_tab = tk.Frame(notebook, bg=self.COLOR_CARD)
        notebook.add(log_tab, text="  Ground Link Log  ")

        header = tk.Frame(log_tab, bg=self.COLOR_CARD_BORDER, height=28)
        header.pack(side=tk.TOP, fill=tk.X)

        btn_clear_log = tk.Button(
            header,
            text="Clear Log",
            font=("Segoe UI", 8, "bold"),
            bg="#1e293b",
            fg=self.COLOR_FG_BRIGHT,
            activebackground="#334155",
            activeforeground="#ffffff",
            relief="flat",
            command=self._clear_log,
        )
        btn_clear_log.pack(side=tk.RIGHT, padx=8, pady=2)

        self.txt_console = tk.Text(
            log_tab,
            bg="#090d16",
            fg="#f8fafc",
            font=("Consolas", 10),
            wrap=tk.WORD,
            borderwidth=0,
            highlightthickness=0,
            padx=12,
            pady=8,
        )

        # High-contrast color tags for log levels
        self.txt_console.tag_configure("INFO", foreground="#38bdf8")
        self.txt_console.tag_configure("ERROR", foreground="#f87171", font=("Consolas", 10, "bold"))
        self.txt_console.tag_configure("WARN", foreground="#fbbf24")
        self.txt_console.tag_configure("SUCCESS", foreground="#34d399", font=("Consolas", 10, "bold"))
        self.txt_console.tag_configure("TEXT", foreground="#f1f5f9")

        scroll = ttk.Scrollbar(log_tab, orient=tk.VERTICAL, command=self.txt_console.yview)
        self.txt_console.configure(yscrollcommand=scroll.set)

        self.txt_console.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    # --------------------------------------------------------------------------
    # Wireless Connection Handling (Real Handshake — Zero Synthetic Connection)
    # --------------------------------------------------------------------------

    def _toggle_connection(self):
        if self.is_connected or (self.client_thread and self.client_thread.is_alive()):
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        host = self.entry_host.get().strip()
        port_str = self.entry_port.get().strip()

        if not host:
            messagebox.showwarning("Connection Input", "Please enter a valid Ground Station IP (e.g. 192.168.4.1).")
            return

        try:
            port = int(port_str)
        except ValueError:
            messagebox.showwarning("Connection Input", "Please enter a numeric port (e.g. 80).")
            return

        # Visual state: Connecting (Amber) — NOT connected yet!
        self.lbl_status_dot.configure(fg=self.COLOR_YELLOW)
        self.lbl_status_text.configure(text=f"Connecting to {host}:{port}...")
        self.btn_connect.configure(text="Connecting...", state="disabled")
        self._log(f"Initiating wireless handshake with SkyEdge Ground Station at http://{host}:{port}/api/status...")

        self.stop_event.clear()
        self.client_thread = WirelessGroundStationThread(
            host=host,
            port=port,
            event_queue=self.event_queue,
            stop_event=self.stop_event,
        )
        self.client_thread.start()

    def _disconnect(self):
        if self.client_thread:
            self.stop_event.set()
            self.client_thread = None

        self.is_connected = False
        self.btn_connect.configure(text="Connect Wireless", style="Connect.TButton", state="normal")
        self.lbl_status_dot.configure(fg=self.COLOR_RED)
        self.lbl_status_text.configure(text="Disconnected")
        self.lbl_telemetry_meta.configure(text="Clients: 0 | Heap: -- KB")
        self._log("Wireless link disconnected.")

    # --------------------------------------------------------------------------
    # Event Queue Processing
    # --------------------------------------------------------------------------

    def _pulse_status_dot(self):
        """Briefly changes the status dot color to indicate wireless activity"""
        self.lbl_status_dot.configure(fg=self.COLOR_ACCENT)
        self.after(150, lambda: self.lbl_status_dot.configure(fg=self.COLOR_GREEN))

    def _poll_event_queue(self):
        """Drains background wireless events from queue and updates UI"""
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()
                
                if event_type == "CONNECTED":
                    self.is_connected = True
                    self.lbl_status_dot.configure(fg=self.COLOR_GREEN)
                    self.lbl_status_text.configure(text="Wireless Connected")
                    self.btn_connect.configure(text="Disconnect", style="Disconnect.TButton", state="normal")
                    self._log(payload)
                    self._show_toast(payload, color=self.COLOR_GREEN)

                elif event_type == "DISCONNECTED":
                    self._disconnect()
                    self._log(payload, level="WARN")

                elif event_type == "ERROR":
                    # REAL Handshake failure: ESP32 was NOT reachable wirelessly!
                    self._disconnect()
                    self._log(payload, level="ERROR")
                    self._show_toast(payload, duration_ms=6000, color=self.COLOR_RED)

                elif event_type == "STATUS_UPDATE":
                    self.last_packet_time = time.time()
                    self._pulse_status_dot()
                    stations = payload.get("stations_connected", 0)
                    free_heap_kb = payload.get("free_heap", 0) // 1024
                    total_rx = payload.get("total_received", 0)
                    self.lbl_telemetry_meta.configure(
                        text=f"Wi-Fi Clients: {stations} | Heap: {free_heap_kb}KB | Total: {total_rx}"
                    )

                elif event_type == "NEW_DETECTION":
                    self.last_packet_time = time.time()
                    self._pulse_status_dot()
                    jpeg_bytes, img_info = payload
                    self._handle_new_detection(jpeg_bytes, img_info)

                elif event_type == "LOG":
                    self._log(payload)

        except queue.Empty:
            pass

        self.after(50, self._poll_event_queue)

    def _handle_new_detection(self, jpeg_bytes: bytes, img_info: dict):
        """Processes real-time reassembled image frame and metadata"""
        img_id = int(img_info.get("image_id", 0))
        event_type = img_info.get("event_type", "UNKNOWN")
        score = float(img_info.get("score", 0.0))
        justification = img_info.get("justification", "")
        now_str = datetime.now().strftime("%H:%M:%S")

        # Parse metrics from real justification string
        conf = 0.0
        sev = 0.0
        urg = 0.0
        mv = 0.0

        if "Conf=" in justification:
            try:
                conf = float(justification.split("Conf=")[1].split(" ")[0].split("|")[0])
            except Exception:
                pass
        if "Sev=" in justification:
            try:
                sev = float(justification.split("Sev=")[1].split(" ")[0].split("|")[0])
            except Exception:
                pass
        if "Urg=" in justification:
            try:
                urg = float(justification.split("Urg=")[1].split(" ")[0].split("|")[0])
            except Exception:
                pass
        if "MV=" in justification:
            try:
                mv = float(justification.split("MV=")[1].split(" ")[0].split("|")[0])
            except Exception:
                pass

        size = (0, 0)
        if PIL_AVAILABLE:
            try:
                pil_img = Image.open(io.BytesIO(jpeg_bytes))
                pil_img.load()
                size = pil_img.size
            except Exception as e:
                self._log(f"PIL decode error on image #{img_id}: {e}", level="ERROR")

        rec = DetectionRecord(
            record_id=img_id,
            received_at=now_str,
            event_type=event_type,
            confidence=conf,
            severity=sev,
            urgency=urg,
            mission_value=mv,
            final_score=score,
            action="TRANSMIT_NOW",
            justification=justification,
            iso_timestamp=now_str,
            raw_image_bytes=jpeg_bytes,
            image_size=size,
            status="COMPLETE",
        )

        self.detections.append(rec)
        self.active_detection = rec

        # Add to History Tree (newest at top) with zebra striping
        hist_tag = "even" if len(self.history_tree.get_children()) % 2 == 0 else "odd"
        self.history_tree.insert(
            "",
            0,
            iid=str(rec.record_id),
            values=(
                rec.record_id,
                rec.event_type.upper(),
                f"{rec.final_score:.1f}",
                rec.received_at,
            ),
            tags=(hist_tag,),
        )

        # Remove standby placeholder from Audit Tree if present
        for child in self.audit_tree.get_children():
            if "info_row" in self.audit_tree.item(child, "tags"):
                self.audit_tree.delete(child)

        # Add to Audit Tree with zebra striping
        audit_tag = "even" if len(self.audit_tree.get_children()) % 2 == 0 else "odd"
        self.audit_tree.insert(
            "",
            0,
            values=(
                now_str,
                f"#{rec.record_id:03d}",
                rec.event_type.upper(),
                f"{rec.final_score:.1f}",
                rec.justification,
            ),
            tags=(audit_tag,),
        )

        # Render on main dashboard
        self._display_detection(rec)
        self._log(
            f"[WIRELESS RX] Received Detection #{img_id} ({event_type.upper()}, Score={score:.1f}, {len(jpeg_bytes)} bytes)"
        )

    # --------------------------------------------------------------------------
    # Display & History UI Updates
    # --------------------------------------------------------------------------

    def _display_detection(self, rec: DetectionRecord):
        """Renders the image and ML justification details in the UI"""
        badge_colors = {
            "fire": ("#ff4d4f", "#3a1314"),
            "wildfire": ("#ff4d4f", "#3a1314"),
            "flood": ("#1890ff", "#0e2a4a"),
            "landslide": ("#fa8c16", "#3f2005"),
            "smoke": ("#c084fc", "#2e1065"),
            "disaster_zone": ("#f5222d", "#380d10"),
            "routine": ("#52c41a", "#133808"),
            "unknown": (self.COLOR_MUTED, "#282c34"),
        }

        fg_color, bg_color = badge_colors.get(
            rec.event_type.lower(), (self.COLOR_YELLOW, "#282c34")
        )
        self.lbl_event_badge.configure(
            text=rec.event_type.upper(), fg=fg_color, bg=bg_color
        )
        self.lbl_event_badge.master.configure(bg=bg_color)

        # Action Badge
        act_color = self.COLOR_GREEN if "TRANSMIT" in rec.action else self.COLOR_ORANGE
        self.lbl_action_badge.configure(text=rec.action, fg=act_color)

        # Numerical Metrics
        self.metric_widgets["lbl_final_score"].configure(
            text=f"{rec.final_score:.2f}"
        )
        self.metric_widgets["lbl_confidence"].configure(
            text=f"{rec.confidence:.2%}" if rec.confidence <= 1.0 else f"{rec.confidence:.2f}"
        )
        self.metric_widgets["lbl_severity"].configure(
            text=f"{rec.severity:.2f}"
        )
        self.metric_widgets["lbl_urgency"].configure(text=f"{rec.urgency:.2f}")
        self.metric_widgets["lbl_mission_value"].configure(
            text=f"{rec.mission_value:.2f}"
        )

        # Update Justification Text
        self.txt_justification.configure(state="normal")
        self.txt_justification.delete("1.0", tk.END)
        self.txt_justification.insert(
            tk.END, rec.justification if rec.justification else "No AI justification text provided."
        )
        self.txt_justification.configure(state="disabled")

        self.lbl_rx_time.configure(
            text=f"Frame ID: #{rec.record_id} | Ground Received: {rec.received_at}"
        )

        # Render Image
        if rec.raw_image_bytes and PIL_AVAILABLE:
            try:
                pil_img = Image.open(io.BytesIO(rec.raw_image_bytes))
                self.current_pil_image = pil_img
                self.lbl_image_meta.configure(
                    text=f"Res: {pil_img.width}x{pil_img.height} | {len(rec.raw_image_bytes):,} bytes | SEQ #{rec.record_id:03d}"
                )
                self._render_current_image()
            except Exception as e:
                self._draw_placeholder(f"Failed to display image: {e}")
        elif rec.raw_image_bytes:
            self._draw_placeholder("Pillow not installed to render image.")
        else:
            self._draw_placeholder("No image associated with this record.")
            self.lbl_image_meta.configure(text="Metadata only")

    def _render_current_image(self):
        """Scales and draws self.current_pil_image onto the canvas proportionally"""
        if not self.current_pil_image or not PIL_AVAILABLE:
            return

        cw = self.image_canvas.winfo_width()
        ch = self.image_canvas.winfo_height()
        if cw < 20 or ch < 20:
            return

        iw, ih = self.current_pil_image.size
        scale = min(cw / iw, ch / ih)
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))

        resized = self.current_pil_image.resize(
            (new_w, new_h), Image.Resampling.LANCZOS
        )
        self.current_photo_image = ImageTk.PhotoImage(resized)

        self.image_canvas.delete("all")
        x = (cw - new_w) // 2
        y = (ch - new_h) // 2
        self.image_canvas.create_image(
            x, y, anchor="nw", image=self.current_photo_image
        )

    def _on_canvas_resize(self, event):
        if self.current_pil_image:
            self._render_current_image()

    def _on_history_select(self, event):
        selected = self.history_tree.selection()
        if not selected:
            return
        rec_id = int(selected[0])
        for rec in self.detections:
            if rec.record_id == rec_id:
                self._display_detection(rec)
                break

    def _clear_history(self):
        self.history_tree.delete(*self.history_tree.get_children())
        self.audit_tree.delete(*self.audit_tree.get_children())
        self.audit_tree.insert(
            "",
            0,
            values=(
                "--:--:--",
                "#000",
                "STANDBY",
                "--",
                "Awaiting incoming telemetry... Live decision justifications will appear here in real-time."
            ),
            tags=("info_row",),
        )
        self.detections.clear()
        self.detection_counter = 0
        self.active_detection = None
        self.current_pil_image = None
        self.current_photo_image = None
        self._draw_placeholder("History cleared.")
        self.lbl_event_badge.configure(text="NONE", fg=self.COLOR_MUTED, bg="#282c34")
        self.lbl_action_badge.configure(text="IDLE", fg=self.COLOR_FG)
        for widget in self.metric_widgets.values():
            widget.configure(text="--")
        self.txt_justification.configure(state="normal")
        self.txt_justification.delete("1.0", tk.END)
        self.txt_justification.insert(tk.END, "Awaiting transmission...")
        self.txt_justification.configure(state="disabled")
        self.lbl_rx_time.configure(text="Received: --")
        self.lbl_image_meta.configure(text="No image received")

    # --------------------------------------------------------------------------
    # Logging
    # --------------------------------------------------------------------------

    def _log(self, text: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lvl = level.upper() if level else "INFO"
        prefix = f"[{ts}] [{lvl}] "
        body = f"{text}\n"

        tag = lvl if lvl in ("INFO", "ERROR", "WARN", "SUCCESS") else "INFO"
        self.txt_console.insert(tk.END, prefix, tag)
        self.txt_console.insert(tk.END, body, "TEXT")
        lines = int(self.txt_console.index("end-1c").split(".")[0])
        if lines > 500:
            self.txt_console.delete("1.0", "50.0")
        self.txt_console.see(tk.END)

    def _clear_log(self):
        self.txt_console.delete("1.0", tk.END)

    def _on_close(self):
        self._disconnect()
        self.destroy()


# ==============================================================================
# Entry Point
# ==============================================================================

def main():
    app = SkyEdgeViewerApp()
    app.mainloop()


if __name__ == "__main__":
    main()

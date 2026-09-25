/*
 * =====================================================================================
 * Project:       SkyEdge Autonomous Edge Intelligence System
 * Component:     Ground Receiver Web Dashboard (ESP32 #2)
 * File:          skyedge_ground_web.ino
 * Description:   Receives chunked image transmissions (HDR, DAT, END) over 2.4 GHz
 *                ESP-NOW protocol, validates and reassembles them, decodes base64 into
 *                raw JPEG bytes, and maintains a strict decision-sequence ordered
 *                15-image FIFO rolling buffer. Hosts an open SoftAP ("SkyEdge-Ground")
 *                and serves a responsive, self-contained web dashboard (no internet).
 *                Contains NO hardcoded or synthetic sample data.
 * =====================================================================================
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <WebServer.h>
#include "mbedtls/base64.h"
#include <esp_arduino_version.h>

// =====================================================================================
// Settings & Constants
// =====================================================================================
#define MAX_IMAGES          15
#define MAX_CHUNKS          120
#define AP_SSID             "SkyEdge-Ground"
#define ESPNOW_CHANNEL      1
#define DEBUG_SERIAL_BAUD   115200

WebServer server(80);

// =====================================================================================
// Data Structures
// =====================================================================================
struct ImageRecord {
    int image_id;               // Monotonic decision sequence number from Raspberry Pi
    String event_type;          // Detected hazard type (fire, flood, landslide, etc.)
    float score;                // Priority score (0 - 100)
    String justification;       // Real-time AI confidence, severity, and decision rationale
    uint8_t *jpeg_data;         // Decoded binary JPEG data
    size_t jpeg_len;            // Byte length of JPEG
    uint32_t timestamp_ms;      // Millis when reassembly completed
    bool is_complete;
};

// 15-slot rolling FIFO buffer
ImageRecord imageBuffer[MAX_IMAGES];
int total_images_received = 0;   // Lifetime counter

// Active reassembly buffer for incoming chunked transmission
struct ActiveReassembly {
    int image_id;
    int total_chunks;
    String event_type;
    float score;
    String justification;
    uint8_t *chunks_data[MAX_CHUNKS];
    size_t chunks_len[MAX_CHUNKS];
    int chunks_received;
    bool active;
    uint32_t last_activity;
} activeRx;

// Non-blocking status reporting timer
unsigned long last_debug_print = 0;

// =====================================================================================
// Buffer Management
// =====================================================================================
void freeImageRecord(int index) {
    if (imageBuffer[index].jpeg_data != nullptr) {
        free(imageBuffer[index].jpeg_data);
        imageBuffer[index].jpeg_data = nullptr;
    }
    imageBuffer[index].image_id = -1;
    imageBuffer[index].jpeg_len = 0;
    imageBuffer[index].is_complete = false;
}

int findImageIndex(int image_id) {
    for (int i = 0; i < MAX_IMAGES; i++) {
        if (imageBuffer[i].is_complete && imageBuffer[i].image_id == image_id) {
            return i;
        }
    }
    return -1;
}

void storeCompletedImage(ActiveReassembly &rx) {
    // 1. Verify complete chunk integrity (no missing chunks or gaps)
    if (rx.chunks_received != rx.total_chunks) {
        Serial.printf("[REASSEMBLY ERROR] Chunk count mismatch for ID: %d (%d/%d received)\n",
                      rx.image_id, rx.chunks_received, rx.total_chunks);
        return;
    }

    for (int i = 0; i < rx.total_chunks; i++) {
        if (rx.chunks_data[i] == nullptr) {
            Serial.printf("[REASSEMBLY ERROR] Missing chunk #%d in ID: %d\n", i, rx.image_id);
            return;
        }
    }

    // 2. Calculate total byte size of decoded JPEG
    size_t total_size = 0;
    for (int i = 0; i < rx.total_chunks; i++) {
        total_size += rx.chunks_len[i];
    }

    if (total_size == 0) {
        Serial.printf("[REASSEMBLY ERROR] Zero byte payload for ID: %d\n", rx.image_id);
        return;
    }

    // 3. Assemble chunks into contiguous JPEG buffer
    uint8_t *full_jpeg = (uint8_t *)malloc(total_size);
    if (!full_jpeg) {
        Serial.println(F("[ERROR] Failed to allocate RAM for assembled JPEG"));
        return;
    }

    size_t offset = 0;
    for (int i = 0; i < rx.total_chunks; i++) {
        memcpy(full_jpeg + offset, rx.chunks_data[i], rx.chunks_len[i]);
        offset += rx.chunks_len[i];
        free(rx.chunks_data[i]);
        rx.chunks_data[i] = nullptr;
    }

    // 4. Find slot in 15-image buffer: empty slot first, or evict lowest image_id (FIFO order)
    int target_slot = -1;
    for (int i = 0; i < MAX_IMAGES; i++) {
        if (!imageBuffer[i].is_complete) {
            target_slot = i;
            break;
        }
    }

    // Evict lowest image_id if all 15 slots are occupied
    if (target_slot == -1) {
        int lowest_id = 2147483647;
        for (int i = 0; i < MAX_IMAGES; i++) {
            if (imageBuffer[i].image_id < lowest_id) {
                lowest_id = imageBuffer[i].image_id;
                target_slot = i;
            }
        }
        Serial.printf("[BUFFER FIFO] Evicting oldest image_id: #%d from slot %d\n", lowest_id, target_slot);
        freeImageRecord(target_slot);
    }

    // 5. Populate record
    imageBuffer[target_slot].image_id = rx.image_id;
    imageBuffer[target_slot].event_type = rx.event_type;
    imageBuffer[target_slot].score = rx.score;
    imageBuffer[target_slot].justification = rx.justification;
    imageBuffer[target_slot].jpeg_data = full_jpeg;
    imageBuffer[target_slot].jpeg_len = total_size;
    imageBuffer[target_slot].timestamp_ms = millis();
    imageBuffer[target_slot].is_complete = true;

    total_images_received++;

    Serial.printf("[COMPLETE RX] image_id: #%d stored in slot %d | Size: %u bytes | Lifetime Total: %d\n",
                  rx.image_id, target_slot, (unsigned int)total_size, total_images_received);
}

// =====================================================================================
// ESP-NOW Receive Callback (Supports Core 2.x and 3.x)
// =====================================================================================
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
void onDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
#else
void onDataRecv(const uint8_t *mac, const uint8_t *incomingData, int len) {
#endif
    if (len <= 0) return;

    char packet_buf[256];
    int copy_len = (len < 255) ? len : 255;
    memcpy(packet_buf, incomingData, copy_len);
    packet_buf[copy_len] = '\0';
    String payload = String(packet_buf);

    // -----------------------------------------------------------------
    // Packet 1: HDR:<image_id>:<total_chunks>:<event_type>:<score>:<justification>
    // -----------------------------------------------------------------
    if (payload.startsWith("HDR:")) {
        int idx1 = payload.indexOf(':', 4);
        int idx2 = payload.indexOf(':', idx1 + 1);
        int idx3 = payload.indexOf(':', idx2 + 1);
        int idx4 = payload.indexOf(':', idx3 + 1);

        if (idx4 > -1) {
            int img_id = payload.substring(4, idx1).toInt();
            int total_chunks = payload.substring(idx1 + 1, idx2).toInt();

            if (total_chunks > MAX_CHUNKS) {
                Serial.printf("[WARN] total_chunks %d exceeds MAX_CHUNKS %d\n", total_chunks, MAX_CHUNKS);
                return;
            }

            // Clean up any lingering active reassembly
            if (activeRx.active) {
                for (int i = 0; i < MAX_CHUNKS; i++) {
                    if (activeRx.chunks_data[i] != nullptr) {
                        free(activeRx.chunks_data[i]);
                        activeRx.chunks_data[i] = nullptr;
                    }
                }
            }

            activeRx.image_id = img_id;
            activeRx.total_chunks = total_chunks;
            activeRx.event_type = payload.substring(idx2 + 1, idx3);
            activeRx.score = payload.substring(idx3 + 1, idx4).toFloat();
            activeRx.justification = payload.substring(idx4 + 1);

            for (int i = 0; i < MAX_CHUNKS; i++) {
                activeRx.chunks_data[i] = nullptr;
                activeRx.chunks_len[i] = 0;
            }
            activeRx.chunks_received = 0;
            activeRx.active = true;
            activeRx.last_activity = millis();

            Serial.printf("[ESP-NOW RX] HDR for image_id: #%d | Chunks: %d | Event: %s | Score: %.2f\n",
                          img_id, total_chunks, activeRx.event_type.c_str(), activeRx.score);
        }
    }
    // -----------------------------------------------------------------
    // Packet 2: DAT:<image_id>:<chunk_index>:<base64_data>
    // -----------------------------------------------------------------
    else if (payload.startsWith("DAT:")) {
        if (!activeRx.active) return;

        int idx1 = payload.indexOf(':', 4);
        int idx2 = payload.indexOf(':', idx1 + 1);

        if (idx2 > -1) {
            int img_id = payload.substring(4, idx1).toInt();
            if (img_id != activeRx.image_id) return; // Mismatch with active transfer

            int chunk_idx = payload.substring(idx1 + 1, idx2).toInt();
            String b64 = payload.substring(idx2 + 1);

            if (chunk_idx >= 0 && chunk_idx < MAX_CHUNKS && activeRx.chunks_data[chunk_idx] == nullptr) {
                size_t out_len = 0;
                size_t alloc_size = (b64.length() * 3 / 4) + 8;
                unsigned char *out_buf = (unsigned char *)malloc(alloc_size);

                if (out_buf) {
                    int ret = mbedtls_base64_decode(out_buf, alloc_size, &out_len,
                                                    (const unsigned char*)b64.c_str(), b64.length());
                    if (ret == 0 && out_len > 0) {
                        activeRx.chunks_data[chunk_idx] = out_buf;
                        activeRx.chunks_len[chunk_idx] = out_len;
                        activeRx.chunks_received++;
                        activeRx.last_activity = millis();
                    } else {
                        free(out_buf);
                        Serial.printf("[DECODE ERROR] Base64 decode failed for chunk %d of ID: %d (ret=%d)\n",
                                      chunk_idx, img_id, ret);
                    }
                }
            }
        }
    }
    // -----------------------------------------------------------------
    // Packet 3: END:<image_id>
    // -----------------------------------------------------------------
    else if (payload.startsWith("END:")) {
        int img_id = payload.substring(4).toInt();
        if (activeRx.active && activeRx.image_id == img_id) {
            if (activeRx.chunks_received == activeRx.total_chunks) {
                storeCompletedImage(activeRx);
            } else {
                Serial.printf("[ESP-NOW RX] Incomplete ID: %d (%d/%d chunks received). Discarding.\n",
                              img_id, activeRx.chunks_received, activeRx.total_chunks);
                for (int i = 0; i < MAX_CHUNKS; i++) {
                    if (activeRx.chunks_data[i] != nullptr) {
                        free(activeRx.chunks_data[i]);
                        activeRx.chunks_data[i] = nullptr;
                    }
                }
            }
            activeRx.active = false;
        }
    }
}

// =====================================================================================
// Embedded Ground Station Dashboard (Zero Internet, Zero CDNs, Self-Contained)
// =====================================================================================
const char* html_page = R"rawliteral(<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SkyEdge Ground Station</title>
    <style>
        :root {
            --bg-base: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #162036;
            --border-color: #1e293b;
            --border-highlight: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-blue: #38bdf8;
            --accent-glow: rgba(56, 189, 248, 0.15);
            --danger: #ef4444;
            --warning: #f59e0b;
            --success: #10b981;
            --info: #0284c7;
            --purple: #8b5cf6;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }
        .container { max-width: 1300px; margin: 0 auto; }
        
        /* Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: linear-gradient(180deg, #111a2e 0%, var(--bg-card) 100%);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 18px 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        }
        .brand { display: flex; align-items: center; gap: 14px; }
        .logo-badge {
            background: var(--accent-blue);
            color: #000;
            font-weight: 900;
            font-size: 16px;
            letter-spacing: 1px;
            padding: 6px 12px;
            border-radius: 6px;
        }
        h1 { font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }
        h1 span { color: var(--text-muted); font-size: 14px; font-weight: 400; margin-left: 6px; }

        .telemetry-bar {
            display: flex;
            align-items: center;
            gap: 20px;
            font-size: 13px;
        }
        .telemetry-item {
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-secondary);
        }
        .telemetry-val {
            color: var(--text-primary);
            font-weight: 700;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        }
        .pulse-dot {
            width: 9px;
            height: 9px;
            background-color: var(--success);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 10px var(--success);
            animation: pulse 2s infinite ease-in-out;
        }
        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.7; }
            50% { transform: scale(1.25); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.7; }
        }

        /* Toolbar & Controls */
        .controls-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 12px 18px;
            margin-bottom: 24px;
        }
        .ordering-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
            color: var(--text-secondary);
        }
        .toggle-btn {
            background: #1e293b;
            border: 1px solid var(--border-highlight);
            color: var(--text-primary);
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .toggle-btn:hover { background: #334155; }
        .rule-note { font-size: 12px; color: var(--text-muted); }

        /* Gallery Grid */
        .gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
            gap: 22px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .card:hover {
            transform: translateY(-2px);
            border-color: var(--border-highlight);
            box-shadow: 0 8px 24px rgba(0,0,0,0.5);
        }
        .card-img-wrapper {
            position: relative;
            width: 100%;
            height: 230px;
            background: #020617;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            border-bottom: 1px solid var(--border-color);
            cursor: pointer;
        }
        .card-img-wrapper img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            image-rendering: auto;
            transition: transform 0.3s ease;
        }
        .card-img-wrapper:hover img { transform: scale(1.03); }
        .seq-pill {
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(8, 12, 20, 0.85);
            border: 1px solid var(--border-highlight);
            color: var(--accent-blue);
            font-family: ui-monospace, SFMono-Regular, monospace;
            font-size: 11px;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 4px;
            backdrop-filter: blur(4px);
        }
        .size-pill {
            position: absolute;
            top: 10px;
            right: 10px;
            background: rgba(8, 12, 20, 0.85);
            border: 1px solid var(--border-highlight);
            color: var(--text-muted);
            font-family: ui-monospace, SFMono-Regular, monospace;
            font-size: 10px;
            padding: 3px 8px;
            border-radius: 4px;
            backdrop-filter: blur(4px);
        }

        .card-body { padding: 18px; display: flex; flex-direction: column; flex-grow: 1; }
        .card-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }
        .badge {
            font-size: 12px;
            font-weight: 800;
            padding: 4px 10px;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .badge-fire { background: rgba(239, 68, 68, 0.2); color: #fca5a5; border: 1px solid var(--danger); }
        .badge-flood { background: rgba(2, 132, 199, 0.2); color: #7dd3fc; border: 1px solid var(--info); }
        .badge-landslide { background: rgba(245, 158, 11, 0.2); color: #fcd34d; border: 1px solid var(--warning); }
        .badge-smoke { background: rgba(139, 92, 246, 0.2); color: #c4b5fd; border: 1px solid var(--purple); }
        .badge-routine { background: rgba(16, 185, 129, 0.2); color: #6ee7b7; border: 1px solid var(--success); }

        .score-pill {
            font-family: ui-monospace, SFMono-Regular, monospace;
            font-size: 13px;
            font-weight: 700;
        }

        .score-bar-bg {
            width: 100%;
            height: 6px;
            background: #1e293b;
            border-radius: 3px;
            overflow: hidden;
            margin-bottom: 14px;
        }
        .score-bar-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.4s ease;
        }

        .justification-box {
            background: #090e18;
            border: 1px solid var(--border-color);
            border-left: 3px solid var(--accent-blue);
            border-radius: 6px;
            padding: 10px 12px;
            font-size: 12px;
            color: #cbd5e1;
            line-height: 1.5;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            word-break: break-word;
            flex-grow: 1;
        }
        .justification-label {
            color: var(--text-muted);
            font-size: 10px;
            text-transform: uppercase;
            font-weight: 700;
            display: block;
            margin-bottom: 4px;
            letter-spacing: 0.5px;
        }

        /* Empty State */
        .empty-state {
            grid-column: 1 / -1;
            background: var(--bg-card);
            border: 1px dashed var(--border-highlight);
            border-radius: 12px;
            padding: 70px 20px;
            text-align: center;
        }
        .radar-spinner {
            width: 60px;
            height: 60px;
            border: 3px solid rgba(56, 189, 248, 0.15);
            border-top: 3px solid var(--accent-blue);
            border-radius: 50%;
            margin: 0 auto 20px;
            animation: spin 1.8s linear infinite;
        }
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .empty-title { font-size: 18px; font-weight: 600; margin-bottom: 8px; color: var(--text-primary); }
        .empty-subtitle { font-size: 13px; color: var(--text-muted); max-width: 500px; margin: 0 auto; line-height: 1.5; }

        /* Modal */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0; top: 0;
            width: 100%; height: 100%;
            background-color: rgba(0,0,0,0.85);
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .modal-content {
            max-width: 90%;
            max-height: 90%;
            border-radius: 8px;
            border: 1px solid var(--border-highlight);
            box-shadow: 0 10px 40px rgba(0,0,0,0.9);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div class="logo-badge">SKYEDGE</div>
                <div>
                    <h1>GROUND STATION <span>| Edge Intelligence Downlink</span></h1>
                </div>
            </div>
            <div class="telemetry-bar">
                <div class="telemetry-item">
                    <span class="pulse-dot" id="live-pulse"></span>
                    <span id="link-status">ESP-NOW Ch 1</span>
                </div>
                <div class="telemetry-item">
                    <span>WiFi Clients:</span>
                    <span class="telemetry-val" id="stat-clients">0</span>
                </div>
                <div class="telemetry-item">
                    <span>RAM Buffer:</span>
                    <span class="telemetry-val"><span id="stat-buffer">0</span>/15</span>
                </div>
                <div class="telemetry-item">
                    <span>Total Transmitted:</span>
                    <span class="telemetry-val" id="stat-total">0</span>
                </div>
            </div>
        </header>

        <div class="controls-bar">
            <div class="ordering-badge">
                <strong>Timeline View:</strong>
                <span id="order-label">Strict Decision Sequence (Ascending #1 &rarr; #15)</span>
            </div>
            <div>
                <button class="toggle-btn" id="toggle-order-btn" onclick="toggleOrder()">
                    Switch to Newest First
                </button>
            </div>
        </div>

        <div class="gallery" id="gallery-container">
            <div class="empty-state">
                <div class="radar-spinner"></div>
                <div class="empty-title">Awaiting Downlink Transmissions</div>
                <div class="empty-subtitle">
                    Ground Station is actively listening on 2.4 GHz ESP-NOW Channel 1.<br>
                    Autonomous telemetry from airborne unit will render here in real-time.
                </div>
            </div>
        </div>
    </div>

    <!-- Image Inspect Modal -->
    <div class="modal" id="image-modal" onclick="closeModal()">
        <img class="modal-content" id="modal-img" alt="Telemetry Frame">
    </div>

    <script>
        let sortAscending = true;
        let lastReceivedCount = -1;

        function toggleOrder() {
            sortAscending = !sortAscending;
            const btn = document.getElementById('toggle-order-btn');
            const lbl = document.getElementById('order-label');
            if (sortAscending) {
                btn.innerText = "Switch to Newest First";
                lbl.innerText = "Strict Decision Sequence (Ascending #1 \u2192 #15)";
            } else {
                btn.innerText = "Switch to Sequence Order";
                lbl.innerText = "Arrival View (Descending: Newest First)";
            }
            pollImages();
        }

        function getEventBadgeClass(type) {
            const t = (type || '').toLowerCase();
            if (t.includes('fire')) return 'badge-fire';
            if (t.includes('flood')) return 'badge-flood';
            if (t.includes('landslide')) return 'badge-landslide';
            if (t.includes('smoke')) return 'badge-smoke';
            return 'badge-routine';
        }

        function getScoreColor(score) {
            if (score >= 90) return '#ef4444';
            if (score >= 70) return '#f59e0b';
            if (score >= 40) return '#38bdf8';
            return '#10b981';
        }

        function openModal(imgSrc) {
            const modal = document.getElementById('image-modal');
            const modalImg = document.getElementById('modal-img');
            modalImg.src = imgSrc;
            modal.style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('image-modal').style.display = 'none';
        }

        function pollStatus() {
            fetch('/api/status')
                .then(r => r.json())
                .then(d => {
                    document.getElementById('stat-clients').innerText = d.stations_connected || 0;
                    document.getElementById('stat-buffer').innerText = d.buffered_count || 0;
                    document.getElementById('stat-total').innerText = d.total_received || 0;
                })
                .catch(() => {});
        }

        function pollImages() {
            fetch('/api/images')
                .then(r => r.json())
                .then(data => {
                    const gallery = document.getElementById('gallery-container');
                    const images = data.images || [];

                    if (images.length === 0) {
                        gallery.innerHTML = `
                            <div class="empty-state">
                                <div class="radar-spinner"></div>
                                <div class="empty-title">Awaiting Downlink Transmissions</div>
                                <div class="empty-subtitle">
                                    Ground Station is actively listening on 2.4 GHz ESP-NOW Channel 1.<br>
                                    Autonomous telemetry from airborne unit will render here in real-time.
                                </div>
                            </div>
                        `;
                        return;
                    }

                    // Sort images: default ascending by image_id (Pi's decision sequence order)
                    let sorted = [...images];
                    if (sortAscending) {
                        sorted.sort((a, b) => a.image_id - b.image_id);
                    } else {
                        sorted.sort((a, b) => b.image_id - a.image_id);
                    }

                    let html = '';
                    sorted.forEach(img => {
                        const badgeClass = getEventBadgeClass(img.event_type);
                        const scoreColor = getScoreColor(img.score);
                        const sizeKB = (img.size_bytes ? (img.size_bytes / 1024).toFixed(1) + ' KB' : 'Frame');

                        html += `
                            <div class="card">
                                <div class="card-img-wrapper" onclick="openModal('/image/${img.image_id}')">
                                    <span class="seq-pill">SEQ #${String(img.image_id).padStart(3, '0')}</span>
                                    <span class="size-pill">${sizeKB}</span>
                                    <img src="/image/${img.image_id}" alt="Detection #${img.image_id}" loading="lazy">
                                </div>
                                <div class="card-body">
                                    <div class="card-header-row">
                                        <span class="badge ${badgeClass}">${img.event_type}</span>
                                        <span class="score-pill" style="color: ${scoreColor}">Score: ${img.score.toFixed(1)}</span>
                                    </div>
                                    <div class="score-bar-bg">
                                        <div class="score-bar-fill" style="width: ${Math.min(100, Math.max(5, img.score))}%; background: ${scoreColor};"></div>
                                    </div>
                                    <div class="justification-box">
                                        <span class="justification-label">AI Explainability & Rationale</span>
                                        ${img.justification || 'No justification provided.'}
                                    </div>
                                </div>
                            </div>
                        `;
                    });

                    gallery.innerHTML = html;
                })
                .catch(err => {
                    console.error("Poll failed:", err);
                });
        }

        // Initialize polling intervals
        setInterval(pollStatus, 2500);
        setInterval(pollImages, 2500);
        pollStatus();
        pollImages();
    </script>
</body>
</html>
)rawliteral";

// =====================================================================================
// Web Server Handlers
// =====================================================================================
void handleRoot() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "text/html", html_page);
}

void handleApiStatus() {
    String json = "{";
    json += "\"stations_connected\":" + String(WiFi.softAPgetStationNum()) + ",";
    json += "\"free_heap\":" + String(ESP.getFreeHeap()) + ",";
    json += "\"total_received\":" + String(total_images_received) + ",";
    
    int active_buffered = 0;
    for (int i = 0; i < MAX_IMAGES; i++) {
        if (imageBuffer[i].is_complete) active_buffered++;
    }
    json += "\"buffered_count\":" + String(active_buffered) + ",";
    json += "\"channel\":" + String(ESPNOW_CHANNEL) + ",";
    json += "\"uptime_sec\":" + String(millis() / 1000);
    json += "}";

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", json);
}

void handleApiImages() {
    // 1. Gather all completed images
    int valid_indices[MAX_IMAGES];
    int count = 0;
    for (int i = 0; i < MAX_IMAGES; i++) {
        if (imageBuffer[i].is_complete) {
            valid_indices[count++] = i;
        }
    }

    // 2. Strict ordering rule: Sort ascending by image_id (Pi's decision sequence)
    for (int i = 0; i < count - 1; i++) {
        for (int j = i + 1; j < count; j++) {
            if (imageBuffer[valid_indices[i]].image_id > imageBuffer[valid_indices[j]].image_id) {
                int temp = valid_indices[i];
                valid_indices[i] = valid_indices[j];
                valid_indices[j] = temp;
            }
        }
    }

    // 3. Serialize JSON
    String json = "{";
    json += "\"total_received\":" + String(total_images_received) + ",";
    json += "\"buffered_count\":" + String(count) + ",";
    json += "\"images\":[";

    for (int k = 0; k < count; k++) {
        int idx = valid_indices[k];
        if (k > 0) json += ",";
        json += "{";
        json += "\"image_id\":" + String(imageBuffer[idx].image_id) + ",";
        json += "\"event_type\":\"" + imageBuffer[idx].event_type + "\",";
        json += "\"score\":" + String(imageBuffer[idx].score, 2) + ",";
        
        // Escape quotes in justification string
        String safe_just = imageBuffer[idx].justification;
        safe_just.replace("\"", "\\\"");
        json += "\"justification\":\"" + safe_just + "\",";
        json += "\"size_bytes\":" + String(imageBuffer[idx].jpeg_len) + ",";
        json += "\"timestamp_ms\":" + String(imageBuffer[idx].timestamp_ms);
        json += "}";
    }

    json += "]}";

    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(200, "application/json", json);
}

void handleImage() {
    String uri = server.uri();
    int lastSlash = uri.lastIndexOf('/');
    if (lastSlash != -1 && lastSlash < uri.length() - 1) {
        String id_str = uri.substring(lastSlash + 1);
        int target_id = id_str.toInt();

        int idx = findImageIndex(target_id);
        if (idx != -1 && imageBuffer[idx].jpeg_data != nullptr && imageBuffer[idx].jpeg_len > 0) {
            server.sendHeader("Access-Control-Allow-Origin", "*");
            server.sendHeader("Cache-Control", "public, max-age=3600");
            server.send_P(200, "image/jpeg", (const char*)imageBuffer[idx].jpeg_data, imageBuffer[idx].jpeg_len);
            return;
        }
    }
    server.send(404, "text/plain", "Image Not Found");
}

// =====================================================================================
// Setup Routine
// =====================================================================================
void setup() {
    // 1. Initialize USB Serial for local diagnostics
    Serial.begin(DEBUG_SERIAL_BAUD);
    delay(1000);
    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F("   SKYEDGE GROUND STATION RECEIVER & WEB SERVER   "));
    Serial.println(F("=================================================="));

    // Initialize buffer arrays
    for (int i = 0; i < MAX_IMAGES; i++) {
        imageBuffer[i].is_complete = false;
        imageBuffer[i].jpeg_data = nullptr;
        imageBuffer[i].image_id = -1;
    }
    for (int i = 0; i < MAX_CHUNKS; i++) {
        activeRx.chunks_data[i] = nullptr;
    }
    activeRx.active = false;

    // 2. Start WiFi in AP+STA mode & configure Channel 1
    WiFi.mode(WIFI_AP_STA);
    
    IPAddress local_IP(192, 168, 4, 1);
    IPAddress gateway(192, 168, 4, 1);
    IPAddress subnet(255, 255, 255, 0);
    WiFi.softAPConfig(local_IP, gateway, subnet);

    // Explicitly lock SoftAP to Channel 1 (open network, no password)
    WiFi.softAP(AP_SSID, NULL, ESPNOW_CHANNEL);
    esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);

    Serial.printf("[WiFi] SoftAP '%s' active on Channel %d\n", AP_SSID, ESPNOW_CHANNEL);
    Serial.print(F("[WiFi] Ground Station IP: http://"));
    Serial.println(WiFi.softAPIP());
    Serial.print(F("[WiFi] Ground Station MAC Address: "));
    Serial.println(WiFi.macAddress());

    // 3. Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println(F("[ESP-NOW ERROR] Failed to initialize ESP-NOW protocol"));
        return;
    }
    esp_now_register_recv_cb(onDataRecv);
    Serial.println(F("[ESP-NOW] Initialized and listening for downlink telemetry"));

    // 4. Configure Web Server Endpoints
    server.on("/", handleRoot);
    server.on("/api/status", handleApiStatus);
    server.on("/api/images", handleApiImages);
    server.onNotFound([]() {
        if (server.uri().startsWith("/image/")) {
            handleImage();
        } else {
            server.send(404, "text/plain", "404: Endpoint Not Found");
        }
    });

    server.begin();
    Serial.println(F("[WebServer] HTTP Server started on port 80"));
    Serial.println(F("[STATUS] Ready. Open browser to http://192.168.4.1/"));
    Serial.println(F("=================================================="));
}

// =====================================================================================
// Main Execution Loop
// =====================================================================================
void loop() {
    // 1. Handle HTTP client requests
    server.handleClient();

    // 2. Cleanup timed-out reassembly transfers (5-second timeout)
    if (activeRx.active && (millis() - activeRx.last_activity > 5000)) {
        Serial.printf("[ESP-NOW TIMEOUT] ID: %d timed out. Dropping %d/%d chunks.\n",
                      activeRx.image_id, activeRx.chunks_received, activeRx.total_chunks);
        activeRx.active = false;
        for (int i = 0; i < MAX_CHUNKS; i++) {
            if (activeRx.chunks_data[i] != nullptr) {
                free(activeRx.chunks_data[i]);
                activeRx.chunks_data[i] = nullptr;
            }
        }
    }

    // 3. Periodic diagnostic status print every 5 seconds
    if (millis() - last_debug_print >= 5000) {
        last_debug_print = millis();

        int active_buffered = 0;
        for (int i = 0; i < MAX_IMAGES; i++) {
            if (imageBuffer[i].is_complete) active_buffered++;
        }

        int stations = WiFi.softAPgetStationNum();
        Serial.printf("[GROUND STATUS] Connected Clients: %d | Buffer: %d/15 | Total Received: %d | Free Heap: %u bytes\n",
                      stations, active_buffered, total_images_received, (unsigned int)ESP.getFreeHeap());
    }
}

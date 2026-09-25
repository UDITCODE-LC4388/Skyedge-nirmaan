/*
 * =====================================================================================
 * Project:       SkyEdge Autonomous Edge Intelligence System
 * Component:     Downlink Bridge Firmware (Block 8B - ESP32 Airborne Transceiver)
 * File:          skyedge_downlink.ino
 * Target Board:  ESP32 Dev Module (e.g. NodeMCU-32S, ESP32-WROOM-32)
 * Description:   Receives prioritized incident telemetry lines (EVT: and IMG:) from
 *                Raspberry Pi via Hardware UART2 (GPIO 16/17), assigns a strict
 *                monotonic decision-sequence image_id, and broadcasts chunked packets
 *                (HDR, DAT, END) to ground receivers via 2.4 GHz ESP-NOW protocol.
 *                No synthetic or hardcoded values are transmitted.
 * =====================================================================================
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_arduino_version.h>

// =====================================================================================
// Operational Mode & Diagnostics
// =====================================================================================
// TEST_MODE is strictly FALSE for real live operations. No synthetic data is transmitted.
#define TEST_MODE               false
#define ESPNOW_WIFI_CHANNEL     1
#define CHUNK_SIZE              200         // Chars per ESP-NOW data chunk
#define CHUNK_DELAY_MS          10          // Inter-chunk pacing delay to prevent RF overflow

// =====================================================================================
// Pin Assignments & Serial Settings
// =====================================================================================
#define DEBUG_SERIAL_BAUD       115200      // USB Serial Monitor baud rate
#define PI_UART_BAUD            115200      // Baud rate from Raspberry Pi
#define PIN_PI_RX               16          // ESP32 RX2 connected to Raspberry Pi TX (GPIO 14)
#define PIN_PI_TX               17          // ESP32 TX2 connected to Raspberry Pi RX (GPIO 15)
#define PI_UART_NUM             2           // HardwareSerial port 2

HardwareSerial SerialPi(PI_UART_NUM);

// Broadcast MAC address (FF:FF:FF:FF:FF:FF) on Channel 1
const uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// Monotonic sequence number reflecting the Raspberry Pi's AI decision order
int current_image_id = 0;

// Pending metadata from Pi EVT: line
String pending_event_type = "unknown";
float  pending_score = 0.0f;
String pending_justification = "unspecified";
bool   has_pending_evt = false;

// =====================================================================================
// ESP-NOW Send Callback (Compatible with Arduino Core 2.x and 3.x)
// =====================================================================================
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
void onDataSent(const wifi_tx_info_t *tx_info, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        Serial.println(F("[ESP-NOW] Warning: broadcast frame delivery status reported error"));
    }
}
#else
void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        Serial.println(F("[ESP-NOW] Warning: broadcast frame delivery status reported error"));
    }
}
#endif

// =====================================================================================
// Chunked Image Transmission via ESP-NOW
// =====================================================================================
void sendImageInChunks(const String &b64_data) {
    current_image_id++;
    int total_len = b64_data.length();
    int total_chunks = (total_len + CHUNK_SIZE - 1) / CHUNK_SIZE;

    Serial.println(F("--------------------------------------------------"));
    Serial.printf("[TX INITIATED] image_id: #%d | Size: %d b64 chars | Total Chunks: %d\n",
                  current_image_id, total_len, total_chunks);
    Serial.printf("[METADATA] Event: %s | Score: %.2f | Justification: %s\n",
                  pending_event_type.c_str(), pending_score, pending_justification.c_str());

    // 1. Send Header: HDR:<image_id>:<total_chunks>:<event_type>:<score>:<justification>
    String hdr = "HDR:" + String(current_image_id) + ":" + String(total_chunks) + ":" +
                 pending_event_type + ":" + String(pending_score, 2) + ":" + pending_justification;
    
    esp_err_t res = esp_now_send(BROADCAST_MAC, (const uint8_t*)hdr.c_str(), hdr.length());
    if (res != ESP_OK) {
        Serial.printf("[ESP-NOW ERROR] Failed to send HDR: %d\n", res);
    }
    Serial.printf("[TX HEADER] Sent (ID: %d, Chunks: %d)\n", current_image_id, total_chunks);
    delay(15);

    // 2. Send Data Chunks: DAT:<image_id>:<chunk_idx>:<base64_chunk>
    for (int i = 0; i < total_chunks; i++) {
        int start_pos = i * CHUNK_SIZE;
        int chunk_len = ((start_pos + CHUNK_SIZE) <= total_len) ? CHUNK_SIZE : (total_len - start_pos);
        String chunk_substr = b64_data.substring(start_pos, start_pos + chunk_len);

        String dat = "DAT:" + String(current_image_id) + ":" + String(i) + ":" + chunk_substr;
        res = esp_now_send(BROADCAST_MAC, (const uint8_t*)dat.c_str(), dat.length());
        
        delay(CHUNK_DELAY_MS); // Pacing delay to avoid ESP-NOW driver ring buffer drop

        if (i % 10 == 0 || i == total_chunks - 1) {
            Serial.printf("[TX CHUNK] %d/%d (ID: %d)\n", i + 1, total_chunks, current_image_id);
        }
    }

    // 3. Send End Packet: END:<image_id>
    String end_pkt = "END:" + String(current_image_id);
    esp_now_send(BROADCAST_MAC, (const uint8_t*)end_pkt.c_str(), end_pkt.length());
    Serial.printf("[TX END] Transmission complete for image_id: #%d\n", current_image_id);
    Serial.println(F("--------------------------------------------------"));

    // Reset pending event state
    has_pending_evt = false;
    pending_event_type = "unknown";
    pending_score = 0.0f;
    pending_justification = "unspecified";
}

// =====================================================================================
// Setup Routine
// =====================================================================================
void setup() {
    Serial.begin(DEBUG_SERIAL_BAUD);
    delay(500);
    Serial.println();
    Serial.println(F("=================================================="));
    Serial.println(F(" SkyEdge Airborne Downlink Transmitter (ESP32 #1) "));
    Serial.println(F("=================================================="));

    // 1. Initialize Hardware UART2 with Raspberry Pi
    SerialPi.begin(PI_UART_BAUD, SERIAL_8N1, PIN_PI_RX, PIN_PI_TX);
    SerialPi.setTimeout(1500); // 1.5s timeout for complete base64 line reads
    Serial.printf("[UART] Listening on RX=GPIO%d, TX=GPIO%d at %d baud\n", PIN_PI_RX, PIN_PI_TX, PI_UART_BAUD);

    // 2. Initialize WiFi in Station mode & Lock to Channel 1
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();
    esp_wifi_set_channel(ESPNOW_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE);
    Serial.print(F("[WiFi] MAC Address: "));
    Serial.println(WiFi.macAddress());
    Serial.printf("[WiFi] Locked to Channel: %d\n", ESPNOW_WIFI_CHANNEL);

    // 3. Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println(F("[ESP-NOW] ERROR: Failed to initialize protocol"));
        return;
    }
    Serial.println(F("[ESP-NOW] Protocol initialized successfully"));

    esp_now_register_send_cb(onDataSent);

    // 4. Register Broadcast Peer (FF:FF:FF:FF:FF:FF) on Channel 1
    esp_now_peer_info_t peerInfo;
    memset(&peerInfo, 0, sizeof(peerInfo));
    memcpy(peerInfo.peer_addr, BROADCAST_MAC, 6);
    peerInfo.channel = ESPNOW_WIFI_CHANNEL;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        Serial.println(F("[ESP-NOW] ERROR: Failed to register broadcast peer"));
        return;
    }
    Serial.println(F("[ESP-NOW] Broadcast peer registered (FF:FF:FF:FF:FF:FF)"));
    Serial.println(F("[MODE] Real Live Mode ACTIVE: Awaiting Pi EVT: & IMG: lines"));
    Serial.println(F("=================================================="));
}

// =====================================================================================
// Main Execution Loop
// =====================================================================================
void loop() {
    // Listen for incoming telemetry lines from Raspberry Pi over Hardware UART2
    if (SerialPi.available() > 0) {
        String line = SerialPi.readStringUntil('\n');
        line.trim();

        if (line.length() == 0) return;

        // Case 1: EVT:<event_type>|<score>|<justification>
        if (line.startsWith("EVT:")) {
            String content = line.substring(4);
            int p1 = content.indexOf('|');
            int p2 = content.indexOf('|', p1 + 1);

            if (p1 != -1 && p2 != -1) {
                pending_event_type = content.substring(0, p1);
                pending_score = content.substring(p1 + 1, p2).toFloat();
                pending_justification = content.substring(p2 + 1);
                has_pending_evt = true;

                Serial.printf("[UART RX] EVT: %s | Score: %.2f | Justification: %s\n",
                              pending_event_type.c_str(), pending_score, pending_justification.c_str());
            } else {
                Serial.printf("[UART WARN] Malformed EVT packet: %s\n", line.c_str());
            }
        }
        // Case 2: IMG:<base64_data>
        else if (line.startsWith("IMG:")) {
            String b64_payload = line.substring(4);
            b64_payload.trim();

            if (b64_payload.length() > 0) {
                Serial.printf("[UART RX] IMG payload received (%d base64 chars)\n", b64_payload.length());
                sendImageInChunks(b64_payload);
            } else {
                Serial.println(F("[UART WARN] Received empty IMG payload"));
            }
        }
    }
}

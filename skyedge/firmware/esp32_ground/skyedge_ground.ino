/*
 * =====================================================================================
 * Project:       SkyEdge Autonomous Edge Intelligence System
 * Component:     Ground Receiver Firmware (Block 8B - ESP32 Ground Station)
 * File:          skyedge_ground.ino
 * Target Board:  ESP32 Dev Module (e.g. NodeMCU-32S, ESP32-WROOM-32)
 * Description:   Listens on 2.4 GHz for incoming ESP-NOW broadcast telemetry packets
 *                transmitted by the SkyEdge airborne downlink node. Prints received
 *                packets and signal strength (RSSI) to the USB Serial Monitor.
 * =====================================================================================
 */

#include <WiFi.h>
#include <esp_now.h>
#include <esp_arduino_version.h>

// =====================================================================================
// Serial Monitor Settings
// =====================================================================================
#define DEBUG_SERIAL_BAUD   115200

// =====================================================================================
// ESP-NOW Receive Callbacks
// =====================================================================================
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
// ESP32 Arduino Core 3.x (ESP-IDF 5.x) exposes esp_now_recv_info_t with RSSI
void onDataRecv(const esp_now_recv_info_t *info, const uint8_t *incomingData, int len) {
    char packet_buf[256];
    int copy_len = (len < 255) ? len : 255;
    memcpy(packet_buf, incomingData, copy_len);
    packet_buf[copy_len] = '\0';

    int rssi = (info && info->rx_ctrl) ? info->rx_ctrl->rssi : 0;

    Serial.print(F("RX (ESP-NOW): "));
    Serial.print(packet_buf);
    Serial.print(F(" RSSI:"));
    Serial.println(rssi);
}
#else
// ESP32 Arduino Core 2.x (ESP-IDF 4.x) standard callback signature
void onDataRecv(const uint8_t *mac_addr, const uint8_t *incomingData, int len) {
    char packet_buf[256];
    int copy_len = (len < 255) ? len : 255;
    memcpy(packet_buf, incomingData, copy_len);
    packet_buf[copy_len] = '\0';

    Serial.print(F("RX (ESP-NOW): "));
    Serial.println(packet_buf);
}
#endif

// =====================================================================================
// Setup Routine
// =====================================================================================
void setup() {
    // 1. Initialize USB Serial for logging received telemetry
    Serial.begin(DEBUG_SERIAL_BAUD);
    delay(500);
    Serial.println(F("=================================================="));
    Serial.println(F("SkyEdge ESP32 Ground Station Receiver Initialized"));

    // 2. Set WiFi to Station Mode and disconnect from any AP
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    // Print Ground Receiver MAC address for diagnostics
    Serial.print(F("[WiFi] Ground Station MAC Address: "));
    Serial.println(WiFi.macAddress());

    // 3. Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println(F("[ESP-NOW] ERROR: Failed to initialize ESP-NOW protocol"));
        return;
    }
    Serial.println(F("[ESP-NOW] Listening for incoming broadcast packets..."));

    // 4. Register the Receive Callback
    esp_now_register_recv_cb(onDataRecv);

    Serial.println(F("=================================================="));
}

// =====================================================================================
// Main Execution Loop
// =====================================================================================
void loop() {
    // ESP-NOW packet reception is completely interrupt / event driven.
    // The CPU remains idle here between callbacks.
    delay(10);
}

/*
 * =====================================================================================
 * Project:       SkyEdge Autonomous Edge Intelligence System
 * Component:     Downlink Bridge Firmware (Block 8B - ESP32 Onboard Transceiver)
 * File:          skyedge_downlink.ino
 * Target Board:  ESP32 Dev Module (e.g. NodeMCU-32S, ESP32-WROOM-32)
 * Description:   Receives prioritized incident telemetry lines from Raspberry Pi via
 *                Hardware UART2 (GPIO 16/17) and broadcasts them to ground stations
 *                via ESP-NOW protocol (2.4 GHz low-overhead peer-to-peer).
 * =====================================================================================
 */

#include <WiFi.h>
#include <esp_now.h>

// =====================================================================================
// Operational Mode & Diagnostics
// =====================================================================================
// Set to true for bench testing without Raspberry Pi attached (broadcasts test packet every 3s).
// NOTE: Set TEST_MODE to false once this board is actually wired to the Raspberry Pi UART.
#define TEST_MODE               true
#define TEST_PACKET_INTERVAL_MS 3000

// =====================================================================================
// Pin Assignments & Serial Settings
// =====================================================================================
#define DEBUG_SERIAL_BAUD       115200      // USB Serial Monitor baud rate
#define PI_UART_BAUD            115200      // Baud rate from Raspberry Pi (matches config.yaml serial.baud)
#define PIN_PI_RX               16          // ESP32 RX2 connected to Raspberry Pi TX (GPIO 14 / UART0_TXD)
#define PIN_PI_TX               17          // ESP32 TX2 connected to Raspberry Pi RX (GPIO 15 / UART0_RXD)
#define PI_UART_NUM             2           // HardwareSerial port 2

// Hardware UART interface connected to Raspberry Pi
HardwareSerial SerialPi(PI_UART_NUM);

// Broadcast MAC address (no pre-pairing or fixed MAC lookup required)
const uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

#if TEST_MODE
unsigned long last_test_tx_time = 0;
#endif

// =====================================================================================
// ESP-NOW Send Callback (Status check)
// =====================================================================================
void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
    if (status != ESP_NOW_SEND_SUCCESS) {
        Serial.println(F("[ESP-NOW] Broadcast transmission warning: packet delivery failed"));
    }
}

// =====================================================================================
// Setup Routine
// =====================================================================================
void setup() {
    // 1. Initialize USB Serial for local debug logs
    Serial.begin(DEBUG_SERIAL_BAUD);
    delay(500);
    Serial.println(F("=================================================="));
    Serial.println(F("SkyEdge ESP32 Downlink Transmitter Initializing..."));

#if TEST_MODE
    Serial.println(F("[MODE] *** TEST_MODE ACTIVE: Broadcasting simulated packet every 3s ***"));
    Serial.println(F("[MODE] *** Remember to set TEST_MODE to false when wired to Raspberry Pi ***"));
#endif

    // 2. Initialize Hardware UART2 to communicate with Raspberry Pi
    SerialPi.begin(PI_UART_BAUD, SERIAL_8N1, PIN_PI_RX, PIN_PI_TX);
    Serial.printf("[UART] Listening on RX=GPIO%d, TX=GPIO%d at %d baud\n", PIN_PI_RX, PIN_PI_TX, PI_UART_BAUD);

    // 3. Set WiFi to Station Mode and disconnect from any AP
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    // 4. Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println(F("[ESP-NOW] ERROR: Failed to initialize ESP-NOW protocol"));
        return;
    }
    Serial.println(F("[ESP-NOW] Protocol initialized successfully"));

    // Register transmit callback
    esp_now_register_send_cb(onDataSent);

    // 5. Register Broadcast Peer (FF:FF:FF:FF:FF:FF)
    esp_now_peer_info_t peerInfo;
    memset(&peerInfo, 0, sizeof(peerInfo));
    memcpy(peerInfo.peer_addr, BROADCAST_MAC, 6);
    peerInfo.channel = 0;          // Auto / current WiFi channel
    peerInfo.encrypt = false;      // Unencrypted broadcast for ground receivers

    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
        Serial.println(F("[ESP-NOW] ERROR: Failed to register broadcast peer"));
        return;
    }
    Serial.println(F("[ESP-NOW] Broadcast peer registered (FF:FF:FF:FF:FF:FF)"));
    Serial.println(F("=================================================="));
}

// =====================================================================================
// Main Execution Loop
// =====================================================================================
void loop() {
    // -------------------------------------------------------------
    // 1. Periodic Test Mode Broadcast (Simulated packet without Pi)
    // -------------------------------------------------------------
#if TEST_MODE
    unsigned long current_time = millis();
    if (current_time - last_test_tx_time >= TEST_PACKET_INTERVAL_MS) {
        last_test_tx_time = current_time;

        const char* test_packet = "{\"event_type\":\"test\",\"score\":50}";
        esp_err_t result = esp_now_send(BROADCAST_MAC, (const uint8_t*)test_packet, strlen(test_packet));

        // Print local debug line to USB Serial Monitor
        Serial.print(F("TX (ESP-NOW): "));
        Serial.println(test_packet);

        if (result != ESP_OK) {
            Serial.printf("[ESP-NOW] Send error code: %d\n", result);
        }
    }
#endif

    // -------------------------------------------------------------
    // 2. Real UART2 Telemetry Listener from Raspberry Pi
    // -------------------------------------------------------------
    if (SerialPi.available() > 0) {
        String packet = SerialPi.readStringUntil('\n');
        packet.trim();

        if (packet.length() > 0) {
            // ESP-NOW maximum payload is 250 bytes per frame
            size_t send_len = packet.length();
            if (send_len > 250) {
                send_len = 250;
            }

            // Transmit packet over ESP-NOW broadcast
            esp_err_t result = esp_now_send(BROADCAST_MAC, (const uint8_t*)packet.c_str(), send_len);

            // Print local debug line to USB Serial Monitor
            Serial.print(F("TX (ESP-NOW): "));
            Serial.println(packet);

            if (result != ESP_OK) {
                Serial.printf("[ESP-NOW] Send error code: %d\n", result);
            }
        }
    }
}

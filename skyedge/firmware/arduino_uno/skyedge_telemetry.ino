/*
 * =====================================================================================
 * Project:       SkyEdge Autonomous Edge Intelligence System
 * Component:     Telemetry Firmware (Block 8A - Arduino Uno)
 * File:          skyedge_telemetry.ino
 * Target Board:  Arduino Uno (ATmega328P) / Nano
 * Description:   Reads onboard power (ACS712 current & voltage divider), kinematics
 *                (MPU6050), environment (DHT11), and light threshold (LDR digital).
 *                Transmits serialized JSON telemetry once per second over Serial
 *                (115200 baud) to the Raspberry Pi mission controller.
 * =====================================================================================
 */

#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <ArduinoJson.h>

// =====================================================================================
// Pin Assignments (Matches config.yaml sensors.pins)
// =====================================================================================
#define PIN_DHT                 2           // Digital input for DHT11 data line
#define PIN_LDR                 3           // Digital input for LDR DO (threshold)
#define PIN_ACS712              A1          // Analog input for ACS712 current sensor
#define PIN_VOLTAGE             A2          // Analog input for Voltage divider sensor module

// Hardware I2C Pins on Arduino Uno:
// SDA = Pin A4 (MPU6050)
// SCL = Pin A5 (MPU6050)

// =====================================================================================
// Calibration Constants & System Settings (Matches config.yaml sensors section)
// =====================================================================================
#define SERIAL_BAUD                     115200
#define TELEMETRY_INTERVAL_MS           1000

#define ADC_REF_VOLTAGE                 5.0         // 5.0V Arduino Uno analog reference
#define ADC_RESOLUTION                  1023.0      // 10-bit ADC resolution (0-1023)

#define ACS712_SENSITIVITY_MV_PER_AMP   185.0       // 185 mV/A for 5A module (100 for 20A, 66 for 30A)
#define ACS712_ZERO_VOLTAGE             2.5         // Output voltage with 0A flowing (VCC / 2)
#define VOLTAGE_DIVIDER_RATIO           5.0         // Typical 5:1 ratio for 0-25V voltage sensor module

#define DHT_TYPE                        DHT11       // DHT11 sensor model

// =====================================================================================
// Sensor Drivers & Global State
// =====================================================================================
Adafruit_MPU6050 mpu;
DHT dht(PIN_DHT, DHT_TYPE);

bool has_mpu6050 = false;
unsigned long last_telemetry_time = 0;

// =====================================================================================
// Setup Routine
// =====================================================================================
void setup() {
    Serial.begin(SERIAL_BAUD);
    Wire.begin();

    // 1. Configure digital pins
    pinMode(PIN_LDR, INPUT);

    // 2. Initialize DHT11 Temperature & Humidity Sensor
    dht.begin();

    // 3. Initialize MPU6050 IMU over I2C (default address 0x68 with AD0 tied to GND)
    if (mpu.begin(0x68)) {
        has_mpu6050 = true;
        mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
        mpu.setGyroRange(MPU6050_RANGE_500_DEG);
        mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
    }
}

// =====================================================================================
// Main Execution Loop
// =====================================================================================
void loop() {
    unsigned long current_time = millis();

    if (current_time - last_telemetry_time >= TELEMETRY_INTERVAL_MS) {
        last_telemetry_time = current_time;

        // -------------------------------------------------------------
        // 1. Read Voltage (Resistor Divider Module on A2)
        // -------------------------------------------------------------
        int raw_v_adc = analogRead(PIN_VOLTAGE);
        float adc_voltage = (raw_v_adc * ADC_REF_VOLTAGE) / ADC_RESOLUTION;
        float real_voltage = adc_voltage * VOLTAGE_DIVIDER_RATIO;

        // -------------------------------------------------------------
        // 2. Read Current in Amps (ACS712 Hall-Effect Sensor on A1)
        // -------------------------------------------------------------
        int raw_acs_adc = analogRead(PIN_ACS712);
        float acs_out_voltage = (raw_acs_adc * ADC_REF_VOLTAGE) / ADC_RESOLUTION;
        float current_amps = (acs_out_voltage - ACS712_ZERO_VOLTAGE) / (ACS712_SENSITIVITY_MV_PER_AMP / 1000.0);

        // -------------------------------------------------------------
        // 3. Read Temperature in Celsius (DHT11 on Pin 2)
        // -------------------------------------------------------------
        float temp_c = dht.readTemperature();
        if (isnan(temp_c)) {
            // Fallback nominal 25.0 C if sensor read fails or disconnected
            temp_c = 25.0;
        }

        // -------------------------------------------------------------
        // 4. Read Ambient Illumination (LDR DO Digital Threshold on Pin 3)
        // -------------------------------------------------------------
        int illumination = digitalRead(PIN_LDR); // 0 or 1 (DO-only threshold)

        // -------------------------------------------------------------
        // 5. Read Kinematics / Acceleration (MPU6050 over I2C)
        // -------------------------------------------------------------
        float accel_x = 0.0;
        float accel_y = 0.0;
        float accel_z = 0.0;

        if (has_mpu6050) {
            sensors_event_t a, g, temp;
            mpu.getEvent(&a, &g, &temp);
            accel_x = a.acceleration.x;
            accel_y = a.acceleration.y;
            accel_z = a.acceleration.z;
        } else {
            // Fallback nominal acceleration (1G along Z-axis)
            accel_x = 0.0;
            accel_y = 0.0;
            accel_z = 9.80;
        }

        // -------------------------------------------------------------
        // 6. Subsystem Placeholders
        // -------------------------------------------------------------
        // NOTE: storage_pct and bandwidth have no dedicated hardware sensor on the Uno.
        // In full deployment, these can be mapped to an external SD card check,
        // battery management pin, or signal strength input from the ESP32 bridge.
        int storage_pct = 0;
        const char* bandwidth = "HIGH";

        // -------------------------------------------------------------
        // 7. Serialize and Transmit JSON over Serial to Raspberry Pi
        // -------------------------------------------------------------
        StaticJsonDocument<256> doc;
        doc["voltage"] = round(real_voltage * 100.0) / 100.0;
        doc["current"] = round(current_amps * 100.0) / 100.0;
        doc["temperature"] = round(temp_c * 10.0) / 10.0;
        doc["illumination"] = illumination;
        doc["accel_x"] = round(accel_x * 100.0) / 100.0;
        doc["accel_y"] = round(accel_y * 100.0) / 100.0;
        doc["accel_z"] = round(accel_z * 100.0) / 100.0;
        doc["storage_pct"] = storage_pct;
        doc["bandwidth"] = bandwidth;

        serializeJson(doc, Serial);
        Serial.println();
    }
}

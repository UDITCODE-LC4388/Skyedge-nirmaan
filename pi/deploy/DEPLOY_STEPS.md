# SkyEdge Raspberry Pi Deployment Checklist

This numbered guide covers setting up and deploying the SkyEdge Autonomous Mission Controller on a fresh Raspberry Pi (e.g. Raspberry Pi 4B / 5).

---

### Step 1: Flash Operating System
1. Download and launch **Raspberry Pi Imager** on your computer.
2. Choose OS: **Raspberry Pi OS Lite (64-bit)** (Debian Bookworm).
3. In OS Customization (gear icon):
   - Set hostname: `skyedge`
   - Set username: `pi`
   - Configure WiFi credentials and enable **SSH** (public-key or password).
4. Select your MicroSD card and click **Write**.

---

### Step 2: Configure Hardware Interfaces (`raspi-config`)
1. Insert MicroSD card into the Pi, connect power, and SSH into the Pi:
   ```bash
   ssh pi@skyedge.local
   ```
2. Open the configuration tool:
   ```bash
   sudo raspi-config
   ```
3. Configure interfaces under **Interface Options**:
   - **Camera**: Enable Camera interface (`libcamera` stack).
   - **Serial Port**:
     - *"Would you like a login shell to be accessible over serial?"* $\rightarrow$ Select **NO**.
     - *"Would you like the serial port hardware to be enabled?"* $\rightarrow$ Select **YES**.
   - **I2C**: Enable I2C (required for the SSD1306 OLED status display).
4. Select **Finish** and reboot:
   ```bash
   sudo reboot
   ```

---

### Step 3: Install System Dependencies & Clone Repository
1. After reboot, reconnect via SSH and update system packages:
   ```bash
   sudo apt update && sudo apt install -y git python3-pip python3-venv python3-opencv libcamera-tools i2c-tools
   ```
2. Clone the repository into `/home/pi/`:
   ```bash
   cd /home/pi
   git clone <REPO_URL> skyedge
   cd /home/pi/skyedge/pi
   ```

---

### Step 4: Set Up Virtual Environment & Dependencies
1. Create and activate a Python virtual environment:
   ```bash
   python3 -m venv venv --system-site-packages
   source venv/bin/activate
   ```
   *(Note: `--system-site-packages` allows Python to access system-level `libcamera` bindings seamlessly).*
2. Install the project requirements:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

---

### Step 5: Verify Model Artifacts & Storage Directories
1. Ensure the edge model weights and runtime folders are present:
   ```bash
   mkdir -p storage/incoming storage/queued storage/transmitted storage/discarded
   ls -la ai/models/best_ncnn_model/
   ls -la ai/models/landslide_segformer/
   ```
2. Run unit tests to confirm the software environment:
   ```bash
   pytest tests/ -v
   ```

---

### Step 6: Install & Start Systemd Service
1. Copy the systemd service unit file to the system directory:
   ```bash
   sudo cp deploy/skyedge.service /etc/systemd/system/skyedge.service
   ```
2. If using the virtual environment, edit `ExecStart` in `/etc/systemd/system/skyedge.service`:
   ```ini
   ExecStart=/home/pi/skyedge/pi/venv/bin/python3 main.py
   ```
3. Reload systemd daemon, enable the service on boot, and start it immediately:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable skyedge.service
   sudo systemctl start skyedge.service
   ```

---

### Step 7: Service Monitoring & Diagnostics
* Check live service status:
  ```bash
  sudo systemctl status skyedge.service
  ```
* Stream live controller loop logs in real time:
  ```bash
  journalctl -u skyedge.service -f
  ```
* Stop or restart the loop:
  ```bash
  sudo systemctl stop skyedge.service
  sudo systemctl restart skyedge.service
  ```

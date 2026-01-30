#!/usr/bin/env bash
set -e

echo "=== RSDESIGN ST7789 Dashboard Installer ==="

# 1. System packages
sudo apt update
sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  fonts-dejavu \
  python3-dev

# 2. Create venv
python3 -m venv st7789-venv
source st7789-venv/bin/activate

# 3. Python deps
pip install --upgrade pip
pip install \
  spidev \
  gpiozero \
  pillow \
  psutil \
  requests

# 4. Permissions
sudo usermod -aG spi,gpio $USER

# 5. Systemd service
sudo tee /etc/systemd/system/st7789-dashboard.service > /dev/null << 'EOF'
[Unit]
Description=RSDESIGN ST7789 Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ronseamon
WorkingDirectory=/home/ronseamon/st7789-rsdesign-dashboard
EnvironmentFile=/home/ronseamon/st7789-rsdesign-dashboard/config.env
ExecStart=/home/ronseamon/st7789-rsdesign-dashboard/st7789-venv/bin/python \
  /home/ronseamon/st7789-rsdesign-dashboard/st7789_rsdesign_color_dashboard_allinone.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable st7789-dashboard.service

echo "=== INSTALL COMPLETE ==="
echo "Reboot recommended."

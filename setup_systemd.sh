#!/bin/bash

# NVR Interface Systemd Service Setup Script
# Run this on your Linux server after copying the nvr-interface.service file

set -e

echo "Setting up NVR Interface as a systemd service..."

# Get the current username and directory
USERNAME=$(whoami)
CURRENT_DIR=$(pwd)

echo "Current user: $USERNAME"
echo "Current directory: $CURRENT_DIR"

# Change ownership of the nvr-interface directory to current user
echo "Setting ownership..."
sudo chown -R "$USERNAME:$USERNAME" "$CURRENT_DIR"

# Copy the service file to systemd
echo "Copying service file to /etc/systemd/system/..."
SERVICE_FILE="$CURRENT_DIR/nvr-interface.service"
SYSTEMD_FILE="/etc/systemd/system/nvr-interface.service"

# Replace placeholders in service file
cp "$SERVICE_FILE" "/tmp/nvr-interface.service"
sed -i "s|User=nvr|User=$USERNAME|g" "/tmp/nvr-interface.service"
sed -i "s|Group=nvr|Group=$USERNAME|g" "/tmp/nvr-interface.service"
sed -i "s|/path/to/your/nvr-interface|$CURRENT_DIR|g" "/tmp/nvr-interface.service"
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$CURRENT_DIR|g" "/tmp/nvr-interface.service"
sed -i "s|Environment=PYTHONPATH=.*|Environment=PYTHONPATH=$CURRENT_DIR|g" "/tmp/nvr-interface.service"

sudo mv "/tmp/nvr-interface.service" "$SYSTEMD_FILE"

# Reload systemd
echo "Reloading systemd..."
sudo systemctl daemon-reload

# Enable the service to start on boot
echo "Enabling service..."
sudo systemctl enable nvr-interface

echo "Setup complete! You can now:"
echo "  - Start the service: sudo systemctl start nvr-interface"
echo "  - Check status: sudo systemctl status nvr-interface"
echo "  - Stop the service: sudo systemctl stop nvr-interface"
echo "  - Restart: sudo systemctl restart nvr-interface"
echo "  - View logs: sudo journalctl -u nvr-interface -f"

echo ""
echo "Note: Make sure MongoDB and other dependencies are running before starting the service."

# Wallbox EVCC Proxy

A WebSocket proxy service that fixes communication issues between electric vehicle wallbox chargers and EVCC (Electric Vehicle Charge Controller) systems.

## Quick Start

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/SensorsIot/Wallbox-EVCC-Proxy.git
   cd Wallbox-EVCC-Proxy
   ```

2. Make the script executable:
   ```bash
   chmod +x ocpp_proxy.py
   ```

3. Create systemd service file:
   ```bash
   sudo tee /etc/systemd/system/ocpp-proxy.service > /dev/null << 'EOF'
   [Unit]
   Description=OCPP WebSocket Proxy
   After=network.target

   [Service]
   Type=simple
   User=root
   WorkingDirectory=/home/OCPP-Proxy
   ExecStart=/home/OCPP-Proxy/ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887
   Restart=always
   RestartSec=5
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   EOF
   ```

4. Enable and start the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable ocpp-proxy.service
   sudo systemctl start ocpp-proxy.service
   ```

### Usage

**Manual run:**
```bash
./ocpp_proxy.py --listen-port 8888 --target-host 192.168.0.202 --target-port 8887
```

**Service management:**
```bash
sudo systemctl status ocpp-proxy.service    # Check status
sudo systemctl restart ocpp-proxy.service   # Restart
sudo systemctl stop ocpp-proxy.service      # Stop
```

**View logs:**
```bash
# Service logs
sudo journalctl -u ocpp-proxy.service -f

# OCPP message logs
tail -f /home/OCPP-Proxy/ocpp_messages.log
```

## What It Does

- **Fixes malformed URLs**: Converts `ws://host:port//path` → `ws://host:port/path`
- **Handles OCPP protocol**: Manages OCPP 1.6 subprotocol negotiation
- **Fixes timestamps**: Corrects invalid timestamp formats in OCPP messages
- **Comprehensive logging**: Console and file logging for monitoring
- **Automatic restart**: Runs as systemd service with auto-restart

## Network Setup

```
[Wallbox] ←→ [Proxy:8888] ←→ [EVCC:8887]
```

- **Wallbox**: Connect to proxy at `ws://proxy-host:8888/path`
- **Proxy**: Runs on port 8888, forwards to EVCC
- **EVCC**: Receives cleaned connections on port 8887

## Documentation

- **[Functional Specification](Wallbox-EVCC-Proxy-FSD.md)**: Complete technical specification
- **Configuration**: See command-line options with `./ocpp_proxy.py --help`

## Requirements

- Python 3.7+
- `websockets` library
- Linux with systemd (for service mode)

## License

Open source project for the EV charging community.
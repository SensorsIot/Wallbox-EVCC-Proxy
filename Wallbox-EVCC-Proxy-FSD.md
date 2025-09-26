# Wallbox EVCC Proxy - Functional Specification Document

## 1. Overview

The Wallbox EVCC Proxy is a WebSocket proxy service that acts as an intermediary between electric vehicle wallbox chargers and EVCC (Electric Vehicle Charge Controller) systems. The proxy solves communication issues by cleaning malformed URLs and fixing invalid OCPP message timestamps.

### 1.1 Purpose

- Fix malformed WebSocket URLs with double slashes (e.g., `ws://host:port//path` → `ws://host:port/path`)
- Handle OCPP (Open Charge Point Protocol) subprotocol negotiation
- Fix invalid timestamp formats in OCPP messages
- Provide comprehensive logging for debugging and monitoring
- Ensure reliable communication between wallbox and EVCC systems

### 1.2 Scope

This specification covers the WebSocket proxy functionality, OCPP message processing, logging mechanisms, and system service integration.

## 2. System Architecture

### 2.1 Components

```
[Wallbox] ←→ [WebSocket Proxy] ←→ [EVCC System]
     ↓              ↓                    ↓
Client          Proxy Server         Target Server
192.168.0.120   192.168.0.150:8888  192.168.0.202:8887
```

### 2.2 Technology Stack

- **Language**: Python 3
- **WebSocket Library**: websockets
- **Logging**: Python logging with rotating file handlers
- **Service Management**: systemd
- **Protocol**: OCPP 1.6 over WebSocket

## 3. Functional Requirements

### 3.1 URL Path Cleaning (FR-001)

**Description**: Clean malformed URL paths from wallbox connections

**Input**: WebSocket connection request with path like `//AcTec001`
**Output**: Cleaned path `/AcTec001`

**Processing Rules**:
- Remove leading slashes
- Replace multiple consecutive slashes with single slash
- Ensure single leading slash in final path

### 3.2 WebSocket Proxy (FR-002)

**Description**: Bidirectional message relay between client and target server

**Requirements**:
- Accept WebSocket connections on configurable host:port (default: 0.0.0.0:8888)
- Establish connections to target server (default: 192.168.0.202:8887)
- Relay messages bidirectionally without modification (except timestamp fixes)
- Handle connection lifecycle (open, close, error)
- Support OCPP subprotocol negotiation

### 3.3 OCPP Message Processing (FR-003)

**Description**: Process and fix OCPP protocol messages

**Timestamp Fixing**:
- Detect invalid timestamp formats (e.g., `2024-09-23T21:21:18.000Z`)
- Convert to valid ISO format (e.g., `2024-09-23T21:21:18.000Z`)
- Log timestamp corrections

**Message Types Supported**:
- All OCPP 1.6 message types
- JSON-based message format
- Call, CallResult, and CallError message patterns

### 3.4 Logging System (FR-004)

**Description**: Comprehensive logging for monitoring and debugging

**Console Logging**:
- Connection events (open, close, error)
- Path cleaning operations
- Target server connection status
- Timestamp fix notifications

**File Logging**:
- Dedicated OCPP message log: `/home/OCPP-Proxy/ocpp_messages.log`
- Rotating log files (10MB max, 5 backup files)
- Timestamped entries for all OCPP messages
- Separate from console logs

### 3.5 Service Management (FR-005)

**Description**: System service integration for automatic startup

**systemd Service Features**:
- Automatic startup on system boot
- Service restart on failure
- Configurable restart delay (5 seconds)
- Proper logging to system journal
- Service status monitoring

## 4. Non-Functional Requirements

### 4.1 Performance

- **Latency**: < 10ms additional latency for message relay
- **Throughput**: Support continuous OCPP message flow
- **Concurrent Connections**: Handle multiple wallbox connections

### 4.2 Reliability

- **Availability**: 99.9% uptime with automatic service restart
- **Error Recovery**: Graceful handling of connection failures
- **Data Integrity**: No message loss during normal operations

### 4.3 Scalability

- **Multiple Clients**: Support multiple wallbox connections simultaneously
- **Log Management**: Automatic log rotation to prevent disk space issues
- **Resource Usage**: Minimal CPU and memory footprint

### 4.4 Security

- **Network Security**: Operates within trusted network environment
- **Data Privacy**: No modification or storage of sensitive charging data
- **Access Control**: Service runs with appropriate system privileges

## 5. Interface Specifications

### 5.1 WebSocket Client Interface

**Endpoint**: `ws://proxy-host:8888/path`
**Protocol**: WebSocket with optional OCPP subprotocol
**Authentication**: None (trusted network)

### 5.2 WebSocket Target Interface

**Endpoint**: `ws://target-host:8887/cleaned-path`
**Protocol**: WebSocket with OCPP 1.6 subprotocol
**Connection**: Automatic establishment when client connects

### 5.3 Command Line Interface

```bash
./ocpp_proxy.py [options]

Options:
  --listen-host HOST    Host to listen on (default: 0.0.0.0)
  --listen-port PORT    Port to listen on (default: 8888)
  --target-host HOST    Target server host (default: 192.168.0.202)
  --target-port PORT    Target server port (default: 8887)
```

## 6. Error Handling

### 6.1 Connection Errors

- **Client Disconnect**: Clean proxy connection closure
- **Target Unavailable**: Log error and close client connection
- **Network Timeout**: Automatic reconnection attempts

### 6.2 Message Processing Errors

- **Invalid JSON**: Log error, forward message unchanged
- **Timestamp Parse Error**: Log warning, attempt best-effort fix
- **Protocol Errors**: Log and forward to maintain transparency

## 7. Deployment Specifications

### 7.1 System Requirements

- **OS**: Linux with systemd support
- **Python**: 3.7+ with websockets library
- **Network**: Access to both client and target networks
- **Storage**: Sufficient space for rotating log files

### 7.2 Installation

1. Place `ocpp_proxy.py` in `/home/OCPP-Proxy/`
2. Set executable permissions
3. Install systemd service file
4. Enable and start service

### 7.3 Configuration

- Default configuration suitable for most deployments
- Command-line arguments for custom network settings
- Systemd service file for production deployment

## 8. Monitoring and Maintenance

### 8.1 Log Monitoring

- **Console Logs**: `journalctl -u ocpp-proxy.service -f`
- **OCPP Logs**: `tail -f /home/OCPP-Proxy/ocpp_messages.log`

### 8.2 Health Checks

- **Service Status**: `systemctl status ocpp-proxy.service`
- **Process Monitoring**: Check for proxy process and port binding
- **Connection Verification**: Monitor successful client-target connections

### 8.3 Maintenance Tasks

- Monitor log file sizes (automatic rotation configured)
- Review error logs for recurring issues
- Update service configuration as network topology changes

## 9. Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2024-09-26 | Initial production release with full functionality |
| 0.9 | 2024-09-25 | Added timestamp fixing and enhanced logging |
| 0.8 | 2024-09-24 | Basic proxy functionality with URL cleaning |

---

*This document serves as the authoritative specification for the Wallbox EVCC Proxy system.*
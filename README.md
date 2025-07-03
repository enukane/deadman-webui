# Deadman Web UI

Viewer for deaman logs

![example_screenshot](https://github.com/enukane/deadman-webui/blob/main/assets/example_screenshot.png?raw=true)

## Installation

1. Clone or download the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   Note: Only Flask is required as a dependency.

## Usage

### Basic Usage

#### Run deadman in background

```bash
deadman -a -l /path/to/log/directory /path/to/deadman-config-file
```

#### Run deadman-webui

```bash
python deadman-webui.py -l /path/to/log/directory
```

### With deadman Configuration File

```bash
python deadman-webui.py -l /path/to/log/directory -c /path/to/config/file
```

Specifying deadman config file would sort a list of hosts in WebUI according to the order of hosts in the file.

### Command Line Options

- `-l, --log-dir`: Directory containing deadman log files (required)
- `-c, --config`: Deadman configuration file (optional)
- `-n, --name`: Dashboard title (default: "Deadman Monitoring")
- `-p, --port`: Port to run the web server on (default: 8080)
- `-H, --host`: Host to bind the web server to (default: 127.0.0.1)
- `--debug`: Run in debug mode

### Example

```bash
python deadman-webui.py -l /var/log/deadman -c /etc/deadman.conf -n "Production Network" -p 8080
```

## Dashboard Features

### Real-time Updates
- Automatic refresh every 1-10 seconds (configurable)
- Live status indicators for each host
- Real-time statistics in the header

### Status Detection
- **Up**: Host is responding with valid RTT values
- **Down**: Host is not responding (RTT = 0 or repeated identical values)
- **Stale**: No updates received in the last 5 seconds
- **Unknown**: No data available

### Loss Rate Calculation
- Intelligent detection of packet loss
- Historical tracking over recent measurement window
- Visual indicators for high/medium/low loss rates

### Sparkline Charts
- RTT trend visualization for each host
- Configurable time ranges (1m, 3m, 5m, 10m)
- Green bars for successful pings, red bars for losses
- Responsive width based on available screen space

## API Endpoints

### GET /
Main dashboard interface

### GET /api/monitors
Returns JSON array of all monitored hosts with current status

### GET /api/monitor/<target>
Returns detailed JSON data for a specific host

### GET /api/stats
Returns overall monitoring statistics


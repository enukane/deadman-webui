#!/usr/bin/env python3
"""
deadman Web UI - High-capacity table-based monitoring dashboard
Real-time monitoring with 1-second updates for large-scale host monitoring
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import deque

import flask
from flask import Flask, render_template_string, jsonify, request


class DeadmanConfig:
    """Parser for deadman configuration files"""
    
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.targets = {}
        self.target_order = []  # Maintain order from config file
        self._parse_config()
    
    def _parse_config(self):
        """Parse the configuration file"""
        try:
            with open(self.config_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and '\t' in line:
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            name = parts[0]
                            host = parts[1]
                            self.targets[name] = host
                            self.target_order.append(name)
        except FileNotFoundError:
            print(f"Warning: Config file {self.config_path} not found")
        except Exception as e:
            print(f"Error parsing config file: {e}")


class HostMonitor:
    """Monitor class for tracking host statistics and history"""
    
    def __init__(self, name: str, address: str):
        self.name = name
        self.address = address
        self.history = deque(maxlen=600)  # Store up to 600 seconds of data
        self.last_current = 0.0
        self.last_average = 0.0
        self.last_sequence = 0
        self.last_update = None
        self.prev_current = None
        self.prev_average = None
        
    def add_measurement(self, current: float, average: float, sequence: int, timestamp: datetime):
        """Add a new measurement"""
        # Check for loss: RTT is 0 OR both current and average are exactly the same as previous values
        is_loss = (current == 0) or (
            self.prev_current is not None and 
            self.prev_average is not None and
            current == self.prev_current and 
            average == self.prev_average and
            current > 0  # Only apply this rule when RTT values are non-zero
        )
        
        self.history.append({
            'timestamp': timestamp,
            'current': current,
            'average': average,
            'sequence': sequence,
            'is_loss': is_loss
        })
        
        # Update previous values for next comparison
        self.prev_current = current
        self.prev_average = average
        
        self.last_current = current
        self.last_average = average
        self.last_sequence = sequence
        self.last_update = timestamp
    
    def get_loss_rate(self) -> float:
        """Calculate loss rate from recent history"""
        if not self.history:
            return 0.0
        
        total_measurements = len(self.history)
        lost_measurements = sum(1 for h in self.history if h['is_loss'])
        return (lost_measurements / total_measurements) * 100.0
    
    def get_sparkline_data(self, time_range: int = 180) -> List[Dict]:
        """Get data for sparkline chart for specified time range"""
        if time_range >= len(self.history):
            return list(self.history)
        return list(self.history)[-time_range:]
    
    def is_online(self) -> bool:
        """Check if host is currently online"""
        return self.last_current > 0
    
    def get_status_class(self) -> str:
        """Get CSS class for status"""
        if not self.last_update:
            return 'unknown'
        
        # Consider host down if no update in last 5 seconds
        if datetime.now() - self.last_update > timedelta(seconds=5):
            return 'stale'
        
        return 'up' if self.is_online() else 'down'


class LogParser:
    """Enhanced parser for deadman log files with history tracking"""
    
    def __init__(self, log_dir: str):
        self.log_dir = Path(log_dir)
        self.monitors = {}  # host_name -> HostMonitor
        
    def get_available_logs(self) -> List[str]:
        """Get list of available log files"""
        if not self.log_dir.exists():
            return []
        return [f.name for f in self.log_dir.iterdir() if f.is_file()]
    
    def parse_log_file(self, log_name: str, tail_lines: int = 100) -> List[Dict]:
        """Parse a specific log file and return recent entries"""
        log_path = self.log_dir / log_name
        if not log_path.exists():
            return []
        
        entries = []
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                # Get the last N lines
                recent_lines = lines[-tail_lines:] if len(lines) > tail_lines else lines
                
                for line in recent_lines:
                    line = line.strip()
                    if line:
                        parts = line.split()
                        if len(parts) >= 4:
                            timestamp_str = f"{parts[0]} {parts[1]}"
                            try:
                                timestamp = datetime.fromisoformat(timestamp_str)
                                current_value = float(parts[2])
                                average_value = float(parts[3])
                                count = int(parts[4])
                                
                                entries.append({
                                    'timestamp': timestamp,
                                    'current': current_value,
                                    'average': average_value,
                                    'count': count,
                                    'is_loss': current_value == 0
                                })
                            except (ValueError, IndexError):
                                continue
        except Exception as e:
            print(f"Error parsing log file {log_name}: {e}")
        
        return entries
    
    def update_monitor(self, log_name: str, address: str = None):
        """Update monitor with latest data from log file"""
        if log_name not in self.monitors:
            self.monitors[log_name] = HostMonitor(log_name, address or 'unknown')
        
        monitor = self.monitors[log_name]
        if address:
            monitor.address = address
        
        # Get recent entries and update monitor
        entries = self.parse_log_file(log_name, tail_lines=600)  # Last 600 entries
        
        # Add new entries to monitor
        for entry in entries:
            monitor.add_measurement(
                entry['current'],
                entry['average'],
                entry['count'],
                entry['timestamp']
            )
    
    def get_all_monitors(self) -> Dict[str, HostMonitor]:
        """Get all monitors"""
        return self.monitors
    
    def update_all_monitors(self, config_targets: Dict[str, str]):
        """Update all monitors with latest data"""
        available_logs = self.get_available_logs()
        
        for log_name in available_logs:
            address = config_targets.get(log_name, 'unknown')
            self.update_monitor(log_name, address)


# Flask app setup
app = Flask(__name__)

# Global variables
config = None
log_parser = None
app_title = "Deadman Monitoring"

# HTML template for table-based monitoring
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>{{ title }} - Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            margin: 0; 
            padding: 0; 
            background: #f8f9fa; 
            font-size: 14px;
        }
        .container { 
            max-width: 100%; 
            margin: 0 auto; 
            padding: 10px;
        }
        .header { 
            background: linear-gradient(135deg, #2c3e50 0%, #3498db 100%); 
            color: white; 
            padding: 15px 20px; 
            border-radius: 8px; 
            margin-bottom: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { 
            margin: 0; 
            font-size: 24px; 
            font-weight: 300;
        }
        .header .stats {
            display: flex;
            gap: 20px;
            font-size: 14px;
        }
        .stats-item {
            text-align: center;
        }
        .stats-value {
            display: block;
            font-size: 18px;
            font-weight: bold;
        }
        .controls { 
            background: white; 
            padding: 10px 15px; 
            border-radius: 8px; 
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 15px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.12);
        }
        .controls button { 
            padding: 6px 12px; 
            border: none; 
            border-radius: 4px; 
            cursor: pointer; 
            font-size: 14px;
            transition: all 0.2s;
        }
        .controls button.active { 
            background: #3498db; 
            color: white; 
        }
        .controls button:not(.active) { 
            background: #ecf0f1; 
            color: #2c3e50;
        }
        .controls button:hover {
            transform: translateY(-1px);
        }
        .controls select {
            padding: 6px 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }
        .last-update {
            color: #7f8c8d;
            font-size: 12px;
            margin-left: auto;
        }
        .monitoring-table {
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .table-container {
            overflow-x: auto;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            background: #34495e;
            color: white;
            padding: 8px 8px;
            text-align: left;
            font-weight: 600;
            white-space: nowrap;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        td {
            padding: 6px 8px;
            border-bottom: 1px solid #ecf0f1;
            white-space: nowrap;
        }
        tr:hover {
            background: #f8f9fa;
        }
        .status-cell {
            text-align: center;
            font-weight: bold;
            text-transform: uppercase;
            font-size: 11px;
            padding: 4px 8px;
            border-radius: 3px;
        }
        .status-up { background: #d4edda; color: #155724; }
        .status-down { background: #f8d7da; color: #721c24; }
        .status-stale { background: #fff3cd; color: #856404; }
        .status-unknown { background: #e2e3e5; color: #383d41; }
        .loss-rate {
            text-align: right;
            font-weight: bold;
        }
        .loss-rate.high { color: #dc3545; }
        .loss-rate.medium { color: #fd7e14; }
        .loss-rate.low { color: #28a745; }
        .rtt-value {
            text-align: right;
            font-family: 'Courier New', monospace;
        }
        .sequence {
            text-align: right;
            color: #6c757d;
            font-family: 'Courier New', monospace;
        }
        .sparkline-container {
            width: calc(100vw - 600px);
            min-width: 400px;
            height: 35px;
            position: relative;
        }
        .sparkline {
            width: 100%;
            height: 100%;
        }
        .no-data {
            text-align: center;
            color: #6c757d;
            font-style: italic;
            padding: 40px;
        }
        .host-name {
            font-weight: 600;
            color: #2c3e50;
        }
        .host-address {
            color: #7f8c8d;
            font-size: 12px;
        }
        @media (max-width: 768px) {
            .header {
                flex-direction: column;
                text-align: center;
                gap: 10px;
            }
            .controls {
                flex-wrap: wrap;
                gap: 10px;
            }
            .stats {
                flex-direction: column;
                gap: 10px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>{{ title }}</h1>
                <div style="font-size: 14px; opacity: 0.9;">Real-time network monitoring with 1-second updates</div>
            </div>
            <div class="stats">
                <div class="stats-item">
                    <span class="stats-value" id="totalHosts">0</span>
                    <span>Total Hosts</span>
                </div>
                <div class="stats-item">
                    <span class="stats-value" id="upHosts">0</span>
                    <span>Online</span>
                </div>
                <div class="stats-item">
                    <span class="stats-value" id="downHosts">0</span>
                    <span>Offline</span>
                </div>
            </div>
        </div>
        
        <div class="controls">
            <button id="autoRefresh" class="active" onclick="toggleAutoRefresh()">Auto Refresh: ON</button>
            <button onclick="refreshData()">Refresh Now</button>
            <label>Update Interval: 
                <select id="refreshInterval" onchange="updateRefreshInterval()">
                    <option value="1000" selected>1s</option>
                    <option value="2000">2s</option>
                    <option value="5000">5s</option>
                    <option value="10000">10s</option>
                </select>
            </label>
            <label>Time Range: 
                <select id="timeRange" onchange="updateTimeRange()">
                    <option value="60">1m</option>
                    <option value="180" selected>3m</option>
                    <option value="300">5m</option>
                    <option value="600">10m</option>
                </select>
            </label>
            <div class="last-update">
                <span id="lastUpdate">Last updated: Never</span>
            </div>
        </div>
        
        <div class="monitoring-table">
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Host</th>
                            <th>Address</th>
                            <th>Status</th>
                            <th>Loss Rate</th>
                            <th>RTT Current</th>
                            <th>RTT Average</th>
                            <th>Sequence</th>
                            <th id="trendHeader">RTT Trend (3m)</th>
                        </tr>
                    </thead>
                    <tbody id="hostTable">
                        <tr>
                            <td colspan="8" class="no-data">Loading host data...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let autoRefresh = true;
        let refreshInterval = 1000; // 1 second default
        let refreshTimer = null;
        let timeRange = 180; // 3 minutes default

        function updateRefreshInterval() {
            refreshInterval = parseInt(document.getElementById('refreshInterval').value);
            if (autoRefresh) {
                startAutoRefresh();
            }
        }

        function updateTimeRange() {
            timeRange = parseInt(document.getElementById('timeRange').value);
            // Update header text
            const timeLabels = {
                60: '1m',
                180: '3m', 
                300: '5m',
                600: '10m'
            };
            document.getElementById('trendHeader').textContent = `RTT Trend (${timeLabels[timeRange]})`;
            // Refresh data to update charts
            refreshData();
        }

        function toggleAutoRefresh() {
            autoRefresh = !autoRefresh;
            const button = document.getElementById('autoRefresh');
            if (autoRefresh) {
                button.textContent = 'Auto Refresh: ON';
                button.classList.add('active');
                startAutoRefresh();
            } else {
                button.textContent = 'Auto Refresh: OFF';
                button.classList.remove('active');
                stopAutoRefresh();
            }
        }

        function startAutoRefresh() {
            stopAutoRefresh();
            refreshTimer = setInterval(refreshData, refreshInterval);
        }

        function stopAutoRefresh() {
            if (refreshTimer) {
                clearInterval(refreshTimer);
                refreshTimer = null;
            }
        }

        function refreshData() {
            fetch(`/api/monitors?time_range=${timeRange}`)
                .then(response => response.json())
                .then(data => {
                    updateTable(data);
                    updateStats(data);
                    document.getElementById('lastUpdate').textContent = 
                        'Last updated: ' + new Date().toLocaleTimeString();
                })
                .catch(error => {
                    console.error('Error fetching data:', error);
                    document.getElementById('lastUpdate').textContent = 
                        'Error: ' + error.message;
                });
        }

        function updateStats(data) {
            const total = data.length;
            let up = 0, down = 0;
            
            data.forEach(host => {
                if (host.status === 'up') up++;
                else if (host.status === 'down') down++;
            });
            
            document.getElementById('totalHosts').textContent = total;
            document.getElementById('upHosts').textContent = up;
            document.getElementById('downHosts').textContent = down;
        }

        function updateTable(data) {
            const tbody = document.getElementById('hostTable');
            
            if (!data || data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="no-data">No host data available</td></tr>';
                return;
            }
            
            const rows = data.map(host => {
                const lossRateClass = host.loss_rate > 10 ? 'high' : 
                                    host.loss_rate > 1 ? 'medium' : 'low';
                
                return `
                    <tr>
                        <td>
                            <div class="host-name">${host.name}</div>
                        </td>
                        <td>
                            <div class="host-address">${host.address}</div>
                        </td>
                        <td>
                            <span class="status-cell status-${host.status}">${host.status}</span>
                        </td>
                        <td class="loss-rate ${lossRateClass}">
                            ${host.loss_rate.toFixed(1)}%
                        </td>
                        <td class="rtt-value">
                            ${host.last_current > 0 ? host.last_current.toFixed(2) + 'ms' : '-'}
                        </td>
                        <td class="rtt-value">
                            ${host.last_average > 0 ? host.last_average.toFixed(2) + 'ms' : '-'}
                        </td>
                        <td class="sequence">
                            ${host.last_sequence}
                        </td>
                        <td>
                            <div class="sparkline-container">
                                <canvas class="sparkline" width="1000" height="35" data-host="${host.name}"></canvas>
                            </div>
                        </td>
                    </tr>
                `;
            });
            
            tbody.innerHTML = rows.join('');
            
            // Update sparklines
            data.forEach(host => {
                const canvas = document.querySelector(`canvas[data-host="${host.name}"]`);
                if (canvas) {
                    drawSparkline(canvas, host.sparkline_data);
                }
            });
        }

        function drawSparkline(canvas, data) {
            const ctx = canvas.getContext('2d');
            const width = canvas.width;
            const height = canvas.height;
            
            ctx.clearRect(0, 0, width, height);
            
            if (!data || data.length === 0) {
                return;
            }
            
            // Reverse the data array so newest is on the left
            const reversedData = [...data].reverse();
            
            const barWidth = Math.max(1, width / reversedData.length);
            const maxValue = 100; // Fixed maximum value
            const minValue = 0;   // Fixed minimum value
            
            // Draw bars for each data point
            reversedData.forEach((d, i) => {
                const x = (i / reversedData.length) * width;
                
                if (d.is_loss || d.current === 0) {
                    // Loss: draw red bar at maximum height (RTT 100)
                    ctx.fillStyle = '#dc3545';
                    const barHeight = height - 4; // Full height minus small margin
                    ctx.fillRect(x, height - barHeight, barWidth - 1, barHeight);
                } else {
                    // Valid RTT: draw green bar proportional to RTT value
                    ctx.fillStyle = '#28a745';
                    const rttValue = Math.min(d.current, maxValue); // Cap at 100
                    const barHeight = (rttValue / maxValue) * (height - 4);
                    ctx.fillRect(x, height - barHeight, barWidth - 1, barHeight);
                }
            });
        }

        // Initialize
        document.addEventListener('DOMContentLoaded', function() {
            refreshData();
            startAutoRefresh();
        });
    </script>
</body>
</html>
'''


@app.route('/')
def index():
    """Main table-based monitoring dashboard page"""
    return render_template_string(HTML_TEMPLATE, title=app_title)


@app.route('/api/monitors')
def api_monitors():
    """API endpoint to get all monitor data for table display"""
    if not log_parser:
        return jsonify({'error': 'Log parser not initialized'})
    
    # Get time range parameter
    time_range = int(request.args.get('time_range', 180))
    
    # Update all monitors with latest data
    config_targets = config.targets if config else {}
    log_parser.update_all_monitors(config_targets)
    
    # Get all monitors and format data for table
    monitors = log_parser.get_all_monitors()
    monitor_list = []
    
    # Order monitors according to config file order
    if config and config.target_order:
        # Process monitors in the order they appear in config file
        for name in config.target_order:
            if name in monitors:
                monitor = monitors[name]
                sparkline_data = monitor.get_sparkline_data(time_range)
                
                monitor_list.append({
                    'name': name,
                    'address': monitor.address,
                    'status': monitor.get_status_class(),
                    'loss_rate': monitor.get_loss_rate(),
                    'last_current': monitor.last_current,
                    'last_average': monitor.last_average,
                    'last_sequence': monitor.last_sequence,
                    'last_update': monitor.last_update.isoformat() if monitor.last_update else None,
                    'sparkline_data': sparkline_data
                })
        
        # Add any monitors not in config (log files without config entries)
        for name, monitor in monitors.items():
            if name not in config.target_order:
                sparkline_data = monitor.get_sparkline_data(time_range)
                
                monitor_list.append({
                    'name': name,
                    'address': monitor.address,
                    'status': monitor.get_status_class(),
                    'loss_rate': monitor.get_loss_rate(),
                    'last_current': monitor.last_current,
                    'last_average': monitor.last_average,
                    'last_sequence': monitor.last_sequence,
                    'last_update': monitor.last_update.isoformat() if monitor.last_update else None,
                    'sparkline_data': sparkline_data
                })
    else:
        # Fallback to unsorted order if no config
        for name, monitor in monitors.items():
            sparkline_data = monitor.get_sparkline_data(time_range)
            
            monitor_list.append({
                'name': name,
                'address': monitor.address,
                'status': monitor.get_status_class(),
                'loss_rate': monitor.get_loss_rate(),
                'last_current': monitor.last_current,
                'last_average': monitor.last_average,
                'last_sequence': monitor.last_sequence,
                'last_update': monitor.last_update.isoformat() if monitor.last_update else None,
                'sparkline_data': sparkline_data
            })
    
    return jsonify(monitor_list)


@app.route('/api/monitor/<target>')
def api_monitor_detail(target):
    """API endpoint to get detailed data for a specific monitor"""
    if not log_parser:
        return jsonify({'error': 'Log parser not initialized'})
    
    monitors = log_parser.get_all_monitors()
    if target not in monitors:
        return jsonify({'error': f'Monitor {target} not found'}), 404
    
    monitor = monitors[target]
    history = monitor.get_sparkline_data()
    
    return jsonify({
        'name': target,
        'address': monitor.address,
        'status': monitor.get_status_class(),
        'loss_rate': monitor.get_loss_rate(),
        'last_current': monitor.last_current,
        'last_average': monitor.last_average,
        'last_sequence': monitor.last_sequence,
        'last_update': monitor.last_update.isoformat() if monitor.last_update else None,
        'history': history,
        'history_count': len(history)
    })


@app.route('/api/stats')
def api_stats():
    """API endpoint to get overall monitoring statistics"""
    if not log_parser:
        return jsonify({'error': 'Log parser not initialized'})
    
    monitors = log_parser.get_all_monitors()
    
    total_hosts = len(monitors)
    up_hosts = sum(1 for m in monitors.values() if m.get_status_class() == 'up')
    down_hosts = sum(1 for m in monitors.values() if m.get_status_class() == 'down')
    stale_hosts = sum(1 for m in monitors.values() if m.get_status_class() == 'stale')
    unknown_hosts = total_hosts - up_hosts - down_hosts - stale_hosts
    
    # Calculate average loss rate
    if monitors:
        avg_loss_rate = sum(m.get_loss_rate() for m in monitors.values()) / len(monitors)
    else:
        avg_loss_rate = 0.0
    
    return jsonify({
        'total_hosts': total_hosts,
        'up_hosts': up_hosts,
        'down_hosts': down_hosts,
        'stale_hosts': stale_hosts,
        'unknown_hosts': unknown_hosts,
        'average_loss_rate': avg_loss_rate
    })


def main():
    parser = argparse.ArgumentParser(description='Deadman Web UI Dashboard')
    parser.add_argument('-l', '--log-dir', required=True,
                       help='Directory containing deadman log files')
    parser.add_argument('-c', '--config', 
                       help='Deadman configuration file')
    parser.add_argument('-n', '--name', default='Deadman Monitoring',
                       help='Dashboard title (default: Deadman Monitoring)')
    parser.add_argument('-p', '--port', type=int, default=8080,
                       help='Port to run the web server on (default: 8080)')
    parser.add_argument('-H', '--host', default='127.0.0.1',
                       help='Host to bind the web server to (default: 127.0.0.1)')
    parser.add_argument('--debug', action='store_true',
                       help='Run in debug mode')
    
    args = parser.parse_args()
    
    # Validate log directory
    if not os.path.exists(args.log_dir):
        print(f"Error: Log directory '{args.log_dir}' does not exist")
        sys.exit(1)
    
    # Initialize global objects
    global config, log_parser, app_title
    
    app_title = args.name
    
    if args.config:
        config = DeadmanConfig(args.config)
        print(f"Loaded configuration with {len(config.targets)} targets")
    else:
        print("No configuration file specified, using log names as targets")
        config = DeadmanConfig('')
    
    log_parser = LogParser(args.log_dir)
    available_logs = log_parser.get_available_logs()
    print(f"Found {len(available_logs)} log files: {', '.join(available_logs)}")
    
    if not available_logs:
        print("Warning: No log files found in the specified directory")
    
    print(f"Starting web server at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
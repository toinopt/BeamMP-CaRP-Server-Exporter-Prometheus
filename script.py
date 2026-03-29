import logging
import os
import time
from collections import defaultdict
from prometheus_client import start_http_server, Gauge, Info
import requests

VERSION = "1.2.0"

# Configuration variables
LOGGING = os.getenv('LOGGING', 'true').lower() == 'true'

# Configure logging — always log to console, optionally also to file
log_format = '%(asctime)s - %(levelname)s - %(message)s'
if LOGGING:
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler('/var/log/beammp/beammp_players.log'),
            logging.StreamHandler()  # Also log to stdout for docker logs
        ]
    )
    logging.info("Logging enabled.")
else:
    logging.disable(logging.CRITICAL)  # Disable logging if not enabled

# Get port from environment variable or use default value 9584
PORT = int(os.getenv('PORT', '9584'))

# Get server name filter from environment variable or use default value
SERVER_NAME_FILTER = os.getenv('SERVER_NAME_FILTER')

# Log the value of SERVER_NAME_FILTER
logging.info(f"SERVER_NAME_FILTER: {SERVER_NAME_FILTER}")

def fetch_server_data():
    url = "https://backend.beammp.com/servers"
    headers = {"User-Agent": f"BeamMP-Server-Exporter/{VERSION}"}
    try:
        response = requests.post(url, headers=headers)
        response.raise_for_status()  # Raise exception for 4xx or 5xx status codes
        return response.json()
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
    except Exception as err:
        logging.error(f"Other error occurred: {err}")
    return None

def clear_all_metrics():
    """Remove all per-server and per-map label sets and reset totals."""
    global previous_servers, previous_maps
    for sname in previous_servers:
        try:
            server_players_metric.remove(sname)
            server_max_players_metric.remove(sname)
            server_name_metric.remove(sname)
        except KeyError:
            pass
    for map_name in previous_maps:
        try:
            server_map_players_metric.remove(map_name)
        except KeyError:
            pass
    previous_servers = set()
    previous_maps = set()
    total_players_metric.set(0)
    total_max_players_metric.set(0)

def update_metrics():
    global previous_servers, previous_maps, consecutive_failures

    server_data = fetch_server_data()

    # If the API call failed, count consecutive failures.
    # After 3 consecutive failures (~3 minutes), clear all metrics
    # so we stop reporting stale data.
    if server_data is None:
        consecutive_failures += 1
        logging.warning(f"Failed to fetch server data ({consecutive_failures} consecutive failure(s)).")
        if consecutive_failures >= 3:
            logging.warning("3+ consecutive API failures — clearing all metrics.")
            clear_all_metrics()
        return

    consecutive_failures = 0  # Reset on success

    total_players = 0  # Initialize total players counter
    total_max_players = 0  # Initialize total max players counter
    server_map_players = defaultdict(int)  # Initialize dictionary to track players per server map
    current_servers = set()  # Track servers seen this cycle
    current_maps = set()  # Track maps seen this cycle

    for server in server_data:
        sname = server.get('sname', '')
        if not sname or (SERVER_NAME_FILTER and SERVER_NAME_FILTER not in sname):
            continue
        
        players = int(server.get('players', 0))
        total_players += players  # Add players to total players counter
        
        max_players = int(server.get('maxplayers', 0))
        total_max_players += max_players  # Add max players to total max players counter
        
        map_name = server.get('map', 'unknown')
        server_map_players[map_name] += players
        current_maps.add(map_name)
        
        current_servers.add(sname)
        server_name_metric.labels(sname)
        server_players_metric.labels(sname).set(players)  # Track number of players connected
        server_max_players_metric.labels(sname).set(max_players)  # Track max players
    
        players_list = server.get('playerslist', '') or ''
        
        # Log server data in the desired format if logging is enabled
        if LOGGING:
            logging.info(f"{sname} - {players_list} - Players: {players} - Max Players: {max_players}")

    # Remove metrics for servers that are no longer reporting
    stale_servers = previous_servers - current_servers
    for sname in stale_servers:
        logging.info(f"Removing stale metrics for server: {sname}")
        try:
            server_players_metric.remove(sname)
            server_max_players_metric.remove(sname)
            server_name_metric.remove(sname)
        except KeyError:
            pass

    stale_maps = previous_maps - current_maps
    for map_name in stale_maps:
        logging.info(f"Removing stale metrics for map: {map_name}")
        try:
            server_map_players_metric.remove(map_name)
        except KeyError:
            pass

    previous_servers = current_servers
    previous_maps = current_maps

    # Set the total players metric
    total_players_metric.set(total_players)
    
    # Set the total max players metric
    total_max_players_metric.set(total_max_players)
    
    # Set the server map players metric
    for map_name, map_players in server_map_players.items():
        server_map_players_metric.labels(map_name).set(map_players)

if __name__ == '__main__':
    # Define Prometheus metrics
    server_name_metric = Info('beammp_server_name', 'Name of BeamMP servers', ['sname'])
    server_players_metric = Gauge('beammp_server_players', 'Number of players on BeamMP servers', ['sname'])
    server_max_players_metric = Gauge('beammp_server_max_players', 'Max players on BeamMP servers', ['sname'])
    
    # Total players metric
    total_players_metric = Gauge('beammp_total_players', 'Total number of players across all servers')
    
    # Total max players metric
    total_max_players_metric = Gauge('beammp_total_max_players', 'Total max number of players across all servers')
    
    # Server map players metric
    server_map_players_metric = Gauge('beammp_server_map_players', 'Total number of players per server map', ['map'])
    
    # Start HTTP server to expose Prometheus metrics
    start_http_server(PORT)  # Use the port defined in the environment variable or default to 9584
    
    # Track previously seen servers and maps to detect stale metrics
    previous_servers = set()
    previous_maps = set()
    consecutive_failures = 0

    # Update metrics and log player information every 60 seconds
    while True:
        update_metrics()
        logging.info("Player information updated.")
        time.sleep(60)  # Updated interval to 60 seconds

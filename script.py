import logging
import os
import time
import sys
from collections import defaultdict
from prometheus_client import start_http_server, Gauge, Info
import httpx
import certifi
import json

# Configuration variables
LOGGING = os.getenv('LOGGING', 'true').lower() == 'true'

# Configure logging if enabled
if LOGGING:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Logging enabled.")
else:
    logging.disable(logging.CRITICAL) # Disable logging if not enabled

# Get port and server name filter from environment
PORT = int(os.getenv('PORT', '9584'))
SERVER_NAME_FILTER = os.getenv('SERVER_NAME_FILTER')
IGNORE_SSL = os.getenv('IGNORE_SSL', 'false').lower() == 'true'

logging.info(f"SERVER_NAME_FILTER: {SERVER_NAME_FILTER}")

# Create a reusable HTTPX client
client = httpx.Client(
    timeout=10.0,
    verify=certifi.where() if not IGNORE_SSL else False,
    headers={"User-Agent": "BeamMP-Prometheus-Exporter/1.0"}
)

def fetch_server_data():
    url = "https://backend.beammp.com/servers-info/"
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
    except httpx.RequestError as err:
        logging.error(f"Request error occurred: {err}")
    except Exception as err:
        logging.error(f"Unexpected error occurred: {err}")
    return []

def update_metrics():
    global active_servers
    server_data = fetch_server_data()
    new_active_servers = set()
    total_players = 0
    total_max_players = 0
    server_map_players = defaultdict(int)

    for server in server_data:
        sname = server.get('sname')
        if not sname or (SERVER_NAME_FILTER and SERVER_NAME_FILTER not in sname):
            continue

        new_active_servers.add(sname)  # Add to active servers set
        
        players = int(server.get('players'))
        total_players += players  # Add players to total players counter
        
        max_players = int(server.get('maxplayers'))
        total_max_players += max_players  # Add max players to total max players counter
        
        map_name = server.get('map')
        server_map_players[map_name] += players
        
        if sname:
            server_name_metric.labels(sname)
            server_players_metric.labels(sname).set(players)  # Track number of players connected
            server_max_players_metric.labels(sname).set(max_players)  # Track max players
    
        players_list = server.get('playerslist', [])
        if LOGGING:
            logging.info(f"{sname} - {players_list} - Players: {players} - Max Players: {max_players}")

    # Remove stale metrics
    stale_servers = active_servers - new_active_servers
    for stale_server in stale_servers:
        server_players_metric.remove(stale_server)
        server_max_players_metric.remove(stale_server)
        server_name_metric.remove(stale_server)

    active_servers = new_active_servers

    total_players_metric.set(total_players)
    total_max_players_metric.set(total_max_players)

    for map_name, map_players in server_map_players.items():
        server_map_players_metric.labels(map_name).set(map_players)

if __name__ == '__main__':
    active_servers = set()

    server_name_metric = Info('beammp_server_name', 'Name of BeamMP servers', ['sname'])
    server_players_metric = Gauge('beammp_server_players', 'Number of players on BeamMP servers', ['sname'])
    server_max_players_metric = Gauge('beammp_server_max_players', 'Max players on BeamMP servers', ['sname'])
    total_players_metric = Gauge('beammp_total_players', 'Total number of players across all servers')
    total_max_players_metric = Gauge('beammp_total_max_players', 'Total max number of players across all servers')
    server_map_players_metric = Gauge('beammp_server_map_players', 'Total number of players per server map', ['map'])
    
    # Start HTTP server to expose Prometheus metrics
    start_http_server(PORT)  # Use the port defined in the environment variable or default to 9584
    
    # Update metrics and log player information every 60 seconds
    while True:
        logging.info("Starting player information update.")
        update_metrics()
        logging.info("Player information updated.")
        time.sleep(60) # Updated interval to 60 seconds

#!/usr/bin/env python3
"""
IPFS Signal Bot Manager

This script manages IPFS files pinned by the Signal bot. It can:
- Show status of all pinned files
- Pin local files
- Unpin files
- Extend pin duration
- Track download status and expiry times
"""

import os
import sys
import json
import sqlite3
import argparse
import requests
from datetime import datetime, timedelta
from tabulate import tabulate
from typing import List, Dict
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class IPFSManager:
    def __init__(self):
        self.ipfs_api = os.getenv('IPFS_API_URL', 'http://localhost:5001')
        self.in_docker = os.path.exists('/.dockerenv') or os.getenv('DOCKER', '').lower() == 'true'
        
        # Set up paths
        if self.in_docker:
            self.app_root = '/app'
            default_downloads = '/app/downloads'
        else:
            self.app_root = os.path.dirname(os.path.abspath(__file__))
            default_downloads = os.path.join(self.app_root, 'downloads')
        
        self.downloads_dir = os.getenv('IPFS_DOWNLOAD_DIR', default_downloads)
        self.db_path = os.path.join(self.app_root, 'pins.db')
        self.pin_duration = int(os.getenv('PIN_DURATION', '72'))  # hours
        
        # Ensure directories exist
        os.makedirs(self.downloads_dir, exist_ok=True)
        
        # Initialize database
        self._init_database()
        
        logger.info(f"Running in {'Docker' if self.in_docker else 'local'} environment")
        logger.info(f"App root: {self.app_root}")
        logger.info(f"DB path: {self.db_path}")
        logger.info(f"Downloads directory: {self.downloads_dir}")

    def _init_database(self):
        """Initialize SQLite database with timestamp adapter"""
        def adapt_datetime(ts):
            return ts.isoformat()

        def convert_datetime(ts):
            try:
                if isinstance(ts, str):
                    return datetime.fromisoformat(ts)
                return datetime.fromisoformat(ts.decode())
            except (ValueError, AttributeError):
                try:
                    # Try parsing as a regular datetime string
                    return datetime.strptime(ts.decode(), '%Y-%m-%d %H:%M:%S.%f')
                except:
                    return None

        # Register adapters
        sqlite3.register_adapter(datetime, adapt_datetime)
        sqlite3.register_converter("timestamp", convert_datetime)
        
        # Create database and table if they don't exist
        with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS pins (
                cid TEXT PRIMARY KEY,
                pin_time timestamp,
                expire_time timestamp,
                downloaded BOOLEAN DEFAULT FALSE
            )''')

    def pin_local_file(self, file_path: str) -> bool:
        """Pin a local file to IPFS"""
        try:
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                return False
                
            with open(file_path, 'rb') as f:
                response = requests.post(
                    f"{self.ipfs_api}/api/v0/add",
                    files={'file': f}
                )
                
            if response.status_code != 200:
                logger.error(f"Failed to add file to IPFS: {response.text}")
                return False
                
            result = response.json()
            cid = result['Hash']
            
            # Add to database
            now = datetime.now()
            expire_time = now + timedelta(hours=self.pin_duration)
            
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                conn.execute(
                    "INSERT INTO pins (cid, pin_time, expire_time, downloaded) VALUES (?, ?, ?, ?)",
                    (cid, now, expire_time, True)
                )
                
            logger.info(f"Successfully pinned file with CID: {cid}")
            return True
            
        except Exception as e:
            logger.error(f"Error pinning local file: {e}")
            return False

    def get_pinned_files(self) -> List[Dict]:
        """Get list of pinned files with their status"""
        pins = []
        try:
            # First get all entries from the database
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT 
                        cid,
                        pin_time as 'pin_time [timestamp]',
                        expire_time as 'expire_time [timestamp]',
                        downloaded
                    FROM pins
                """).fetchall()
                
                for row in rows:
                    # Convert Row object to dict and handle timestamps
                    pin_info = dict(row)
                    
                    # Calculate hours left
                    if pin_info['expire_time']:
                        time_left = max(0, (pin_info['expire_time'] - datetime.now()).total_seconds() / 3600)
                    else:
                        time_left = 0
                        
                    # Check if file exists in downloads directory
                    file_exists = os.path.exists(os.path.join(self.downloads_dir, pin_info['cid']))
                    
                    pins.append({
                        "cid": pin_info['cid'],
                        "pin_time": pin_info['pin_time'],
                        "expire_time": pin_info['expire_time'],
                        "hours_left": time_left,
                        "downloaded": file_exists or pin_info['downloaded']
                    })

            # Try to get IPFS pins if daemon is running
            try:
                response = requests.post(f"{self.ipfs_api}/api/v0/pin/ls", timeout=2)
                if response.status_code == 200:
                    pinned_cids = response.json().get("Keys", {}).keys()
                    # Add any IPFS pins that aren't in our database
                    db_cids = {p["cid"] for p in pins}
                    for cid in pinned_cids:
                        if cid not in db_cids:
                            file_exists = os.path.exists(os.path.join(self.downloads_dir, cid))
                            pins.append({
                                "cid": cid,
                                "pin_time": None,
                                "expire_time": None,
                                "hours_left": 0,
                                "downloaded": file_exists
                            })
            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                logger.warning(f"Could not connect to IPFS daemon: {e}")
                # Continue with just database entries
                    
        except Exception as e:
            logger.error(f"Error getting pinned files: {e}")
        return pins

    def get_file_size(self, cid: str) -> str:
        """Get human-readable file size for a CID"""
        try:
            # First check local file
            local_path = os.path.join(self.downloads_dir, cid)
            if os.path.exists(local_path):
                size_bytes = os.path.getsize(local_path)
            else:
                # If not local, check IPFS
                response = requests.post(
                    f"{self.ipfs_api}/api/v0/object/stat",
                    params={"arg": cid}
                )
                if response.status_code != 200:
                    return "Unknown"
                size_bytes = response.json().get("CumulativeSize", 0)
            
            # Convert to human readable
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size_bytes < 1024:
                    return f"{size_bytes:.1f}{unit}"
                size_bytes /= 1024
            return f"{size_bytes:.1f}TB"
        except Exception as e:
            logger.error(f"Error getting file size: {e}")
            return "Unknown"

    def extend_pin(self, cid: str, hours: int) -> bool:
        """Extend pin duration for a CID"""
        try:
            with sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES) as conn:
                row = conn.execute(
                    "SELECT expire_time FROM pins WHERE cid = ?",
                    (cid,)
                ).fetchone()
                
                if not row:
                    logger.error(f"CID {cid} not found in database")
                    return False
                
                current_expire = row[0]
                new_expire = current_expire + timedelta(hours=hours)
                
                conn.execute(
                    "UPDATE pins SET expire_time = ? WHERE cid = ?",
                    (new_expire, cid)
                )
                logger.info(f"Extended pin duration for {cid} by {hours} hours")
                return True
                
        except Exception as e:
            logger.error(f"Error extending pin: {e}")
            return False

    def unpin_file(self, cid: str) -> bool:
        """Unpin a file from IPFS and clean up local data"""
        success = True
        try:
            # Try to remove from IPFS if it's pinned
            try:
                response = requests.post(
                    f"{self.ipfs_api}/api/v0/pin/rm",
                    params={"arg": cid}
                )
                if response.status_code != 200:
                    error_msg = response.json().get("Message", "Unknown error")
                    if "not pinned" not in error_msg.lower():
                        logger.error(f"Failed to unpin from IPFS: {error_msg}")
                        success = False
                else:
                    logger.info(f"Unpinned {cid} from IPFS")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Could not connect to IPFS daemon: {e}")
                success = False
            
            # Remove from database regardless of IPFS status
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM pins WHERE cid = ?", (cid,))
                if conn.total_changes > 0:
                    logger.info(f"Removed {cid} from database")
            
            # Remove local file if exists
            file_path = os.path.join(self.downloads_dir, cid)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted local file for {cid}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error during unpin operation: {e}")
            return False

def print_status(manager: IPFSManager, args):
    """Print system status"""
    pinned_files = manager.get_pinned_files()
    
    table_data = []
    for file in pinned_files:
        cid = file["cid"]
        size = manager.get_file_size(cid)
        status = "✓ Downloaded" if file["downloaded"] else "⋯ Pending"
        
        if file["hours_left"] > 0:
            expiry = f"{file['hours_left']:.1f}h left"
        else:
            expiry = "Expired"
        
        table_data.append([cid, size, status, expiry])

    headers = ["CID", "Size", "Status", "Expiry"]
    print("\nIPFS Files Status:")
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print(f"\nTotal files: {len(pinned_files)}")
    print(f"Download directory: {manager.downloads_dir}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Add subparsers
    subparsers.add_parser('status', help='Show system status')
    
    pin_parser = subparsers.add_parser('pin', help='Pin a local file')
    pin_parser.add_argument('file', help='Path to local file')
    
    unpin_parser = subparsers.add_parser('unpin', help='Unpin a file')
    unpin_parser.add_argument('cid', help='CID of file to unpin')
    
    extend_parser = subparsers.add_parser('extend', help='Extend pin duration')
    extend_parser.add_argument('cid', help='CID of file to extend')
    extend_parser.add_argument('hours', type=int, help='Additional hours to pin')

    args = parser.parse_args()
    manager = IPFSManager()

    try:
        if args.command == 'status':
            print_status(manager, args)
        elif args.command == 'pin':
            manager.pin_local_file(args.file)
        elif args.command == 'unpin':
            manager.unpin_file(args.cid)
        elif args.command == 'extend':
            manager.extend_pin(args.cid, args.hours)
        else:
            parser.print_help()
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    main()
import os
import re
import json
import time
import sqlite3
import logging
import asyncio
import aiohttp
import aiofiles
from urllib.parse import quote
from datetime import datetime, timedelta
from typing import Set, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
SIGNAL_API_URL = os.getenv("SIGNAL_API_URL", "http://localhost:8080")
IPFS_API_URL = os.getenv("IPFS_API_URL", "http://localhost:5001")
MONITOR_CHATS = os.getenv("MONITOR_CHATS").split(",")
IPFS_DOWNLOAD_DIR = os.getenv("IPFS_DOWNLOAD_DIR", "./downloads")
FETCH_INTERVAL = int(os.getenv("FETCH_INTERVAL", "5"))
PIN_DURATION = int(os.getenv("PIN_DURATION", "72"))  # hours

# Initialize SQLite database
DB_PATH = os.path.join(os.path.dirname(IPFS_DOWNLOAD_DIR), 'pins.db')

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
        CREATE TABLE IF NOT EXISTS pins (
            cid TEXT PRIMARY KEY,
            pin_time TIMESTAMP,
            expire_time TIMESTAMP,
            downloaded BOOLEAN DEFAULT FALSE
        )
        ''')

# Keep track of processed messages
processed_messages: Set[str] = set()

def is_valid_cid(text: str) -> Optional[str]:
    """Extract and validate IPFS CID from text."""
    cid_patterns = [
        r'Qm[1-9A-HJ-NP-Za-km-z]{44}',  # CIDv0
        r'bafy[1-9A-HJ-NP-Za-km-z]{44}'  # CIDv1
    ]
    
    for pattern in cid_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None

async def update_pin_status(cid: str, downloaded: bool = False):
    """Update pin status in database"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE pins SET downloaded = ? WHERE cid = ?",
            (downloaded, cid)
        )

async def add_pin_record(cid: str):
    """Add new pin record to database"""
    now = datetime.now()
    expire_time = now + timedelta(hours=PIN_DURATION)
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pins (cid, pin_time, expire_time, downloaded) VALUES (?, ?, ?, ?)",
            (cid, now, expire_time, False)
        )

async def cleanup_expired_pins():
    """Remove expired pins"""
    with sqlite3.connect(DB_PATH) as conn:
        # Get expired CIDs
        expired = conn.execute(
            "SELECT cid FROM pins WHERE expire_time < ?",
            (datetime.now(),)
        ).fetchall()
        
        for (cid,) in expired:
            try:
                # Unpin from IPFS
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{IPFS_API_URL}/api/v0/pin/rm",
                        params={"arg": cid}
                    ) as response:
                        if response.status == 200:
                            logger.info(f"Unpinned expired CID: {cid}")
                            
                # Remove from database
                conn.execute("DELETE FROM pins WHERE cid = ?", (cid,))
                
                # Remove downloaded file if exists
                file_path = os.path.join(IPFS_DOWNLOAD_DIR, cid)
                if os.path.exists(file_path):
                    os.remove(file_path)
                    
            except Exception as e:
                logger.error(f"Error cleaning up CID {cid}: {str(e)}")

async def download_ipfs_content(session: aiohttp.ClientSession, cid: str) -> bool:
    """Download content from IPFS."""
    try:
        logger.info(f"Attempting to download CID: {cid}")
        
        # First get the file name and size
        async with session.post(
            f"{IPFS_API_URL}/api/v0/ls",
            params={"arg": cid}
        ) as response:
            if response.status != 200:
                logger.error(f"Failed to get IPFS info for CID {cid}")
                return False
            
            data = await response.json()
            if not data.get("Objects"):
                logger.error(f"No objects found for CID {cid}")
                return False
            
            # Get file info
            links = data["Objects"][0].get("Links", [])
            file_name = links[0]["Name"] if links else cid
            
        # Create download directory if it doesn't exist
        os.makedirs(IPFS_DOWNLOAD_DIR, exist_ok=True)
            
        # Download the file
        output_path = os.path.join(IPFS_DOWNLOAD_DIR, file_name)
        async with session.post(
            f"{IPFS_API_URL}/api/v0/get",
            params={"arg": cid}
        ) as response:
            if response.status != 200:
                logger.error(f"Failed to download IPFS content for CID {cid}")
                return False
                
            async with aiofiles.open(output_path, 'wb') as f:
                async for chunk in response.content.iter_chunked(8192):
                    await f.write(chunk)
        
        # Update pin status
        await update_pin_status(cid, True)
        logger.info(f"Successfully downloaded {cid} to {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Error downloading IPFS content: {str(e)}")
        return False

async def pin_ipfs_content(session: aiohttp.ClientSession, cid: str) -> bool:
    """Pin content to IPFS."""
    try:
        logger.info(f"Pinning CID: {cid}")
        async with session.post(
            f"{IPFS_API_URL}/api/v0/pin/add",
            params={"arg": cid}
        ) as response:
            if response.status != 200:
                logger.error(f"Failed to pin CID {cid}")
                return False
            
            logger.info(f"Successfully pinned {cid}")
            return True
            
    except Exception as e:
        logger.error(f"Error pinning IPFS content: {str(e)}")
        return False

async def process_message(session: aiohttp.ClientSession, envelope: dict):
    """Process a single message envelope."""
    try:
        # Extract message content from envelope
        sync_message = envelope.get("syncMessage", {})
        sent_message = sync_message.get("sentMessage", {})
        content = sent_message.get("message", "")
        
        if not content:
            return
            
        # Generate unique message ID
        timestamp = envelope.get("timestamp", "")
        source = envelope.get("source", "")
        msg_id = f"{source}-{timestamp}"
        
        # Skip if already processed
        if msg_id in processed_messages:
            return
            
        # Add to processed messages
        processed_messages.add(msg_id)
        
        logger.info(f"Processing message: {content}")
            
        # Check for IPFS CID
        cid = is_valid_cid(content)
        if not cid:
            logger.debug(f"No CID found in message: {content}")
            return
            
        logger.info(f"Found IPFS CID in message: {cid}")
        
        # Add pin record first
        await add_pin_record(cid)
        
        # Pin the content (this starts the download in the background)
        if await pin_ipfs_content(session, cid):
            logger.info(f"Successfully pinned CID: {cid}")
            
            # Start download in background without waiting
            asyncio.create_task(download_ipfs_content(session, cid))
        else:
            logger.error(f"Failed to pin CID: {cid}")
            
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")

async def fetch_messages(session: aiohttp.ClientSession, chat: str):
    """Fetch messages from a specific Signal chat."""
    try:
        chat_encoded = quote(chat)
        url = f"{SIGNAL_API_URL}/v1/receive/{chat_encoded}"
        logger.debug(f"Fetching messages from: {url}")
        
        async with session.get(url) as response:
            if response.status == 200:
                # First get the raw text
                text = await response.text()
                # Only try to parse if we got some content
                if text.strip():
                    try:
                        messages = json.loads(text)
                        if messages:
                            logger.info(f"Received {len(messages)} messages from {chat}")
                            for msg in messages:
                                if "envelope" in msg:
                                    await process_message(session, msg["envelope"])
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse JSON response: {e}")
            else:
                logger.error(f"Failed to fetch messages: {response.status}")
                
    except Exception as e:
        logger.error(f"Error fetching messages for {chat}: {str(e)}")

async def main():
    """Main bot loop."""
    if not MONITOR_CHATS:
        logger.error("No chat numbers configured to monitor!")
        return
        
    # Initialize database
    init_db()
    
    logger.info(f"Starting bot with configuration:")
    logger.info(f"Signal API URL: {SIGNAL_API_URL}")
    logger.info(f"IPFS API URL: {IPFS_API_URL}")
    logger.info(f"Monitoring chats: {MONITOR_CHATS}")
    logger.info(f"Download directory: {IPFS_DOWNLOAD_DIR}")
    logger.info(f"Fetch interval: {FETCH_INTERVAL} seconds")
    logger.info(f"Pin duration: {PIN_DURATION} hours")
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                # Clean up expired pins
                await cleanup_expired_pins()
                
                # Fetch messages from all monitored chats
                for chat in MONITOR_CHATS:
                    await fetch_messages(session, chat)
                    
                # Wait before next fetch
                await asyncio.sleep(FETCH_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}")
                await asyncio.sleep(FETCH_INTERVAL)

if __name__ == "__main__":
    # Create downloads directory if running directly
    os.makedirs(IPFS_DOWNLOAD_DIR, exist_ok=True)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot stopped due to error: {str(e)}")
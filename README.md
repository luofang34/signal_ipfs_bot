# Signal IPFS Bot

A Docker-based bot that monitors Signal chats for IPFS CIDs and automatically pins them for a configurable duration. When it detects an IPFS CID in a Signal message, it automatically pins and downloads the content.

## Features

- 🤖 Monitors specified Signal chats for IPFS CIDs
- 📥 Automatically downloads and pins IPFS content
- ⏱️ Configurable pin duration with auto-cleanup
- 📊 Real-time monitoring and management
- 📁 Local file pinning support
- 🔄 Pin duration extension

## Project Structure
```
signal-ipfs-bot/
├── Dockerfile
├── README.md
├── bot.py                   # Main bot logic
├── docker-compose.yml       # Docker services configuration
├── downloads                # Downloaded files and database
│   └── [downloaded files]
├── ipfs_data                # IPFS node data
├── manage.py                # Management CLI tool
├── pins.db                  # SQLite database for pin tracking
├── requirements.txt         # Python dependencies
└── signal-cli-config        # Signal client configuration
```

## Prerequisites

- Docker
- Docker Compose
- Signal account (for linking the bot)

## Quick Start

1. Clone the repository:
```bash
git clone https://github.com/luofang34/signal_ipfs_bot.git && cd signal_ipfs_bot
```

2. Create .env file and set the following variables:
```bash
echo "SIGNAL_NUMBER=<your Signal number>" >> .env
echo "MONITOR_CHATS=<Signal numbers to monitor>" >> .env
```

3. Start the services:
```bash
docker-compose up -d
```

4. Follow the bot's initialization in logs:
```bash
docker-compose logs -f bot
```

5. Link your Signal device:
- open the link in your browser:
- http://localhost:8080/v1/qrcodelink?device_name=signal-api
- Open Signal on your phone
- Go to Settings > Linked Devices
- Scan the provided QR code or use the link

The bot will automatically:
- Configure itself using the linked Signal account
- Start monitoring for IPFS CIDs
- Download and pin content as received for the configured duration

## Usage

### Sending CIDs via Signal

Simply send a message containing an IPFS CID to the monitored chat. Examples:
```
QmZ4tDuvesekSs4qM5ZBKpXiZGun7S2CYtEZRB3DYXkjGx
bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi
```

### Managing Pins

Use the management script to interact with the system:

1. Check status of all pins:
```bash
docker-compose exec bot python manage.py status
```

2. Pin a local file:
```bash
# Copy file into container
docker cp file.txt signal_ipfs_bot-bot-1:/app/downloads/

# Pin the file
docker-compose exec bot python manage.py pin /app/downloads/file.txt
```

3. Extend pin duration:
```bash
docker-compose exec bot python manage.py extend QmZ4tDuvesekSs4qM5ZBKpXiZGun7S2CYtEZRB3DYXkjGx 24
```

4. Unpin a file:
```bash
docker-compose exec bot python manage.py unpin QmZ4tDuvesekSs4qM5ZBKpXiZGun7S2CYtEZRB3DYXkjGx
```

### Accessing Files

Downloaded files are stored in the `downloads` directory:
```bash
# List downloaded files
ls -l downloads/

# Copy file from container (if needed)
docker cp signal_ipfs_bot-bot-1:/app/downloads/QmHash ./
```

## Configuration

The bot uses these environment variables (automatically configured during setup):
```env
MONITOR_CHATS=+1234567890    # Signal numbers to monitor
SIGNAL_API_URL=http://signal-api:8080
IPFS_API_URL=http://ipfs:5001
DOWNLOAD_DIR=/app/downloads
FETCH_INTERVAL=5             # Message check interval (seconds)
PIN_DURATION=72              # How long to keep pins (hours)
```

## Monitoring

Check service logs:
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f bot
docker-compose logs -f signal-api
docker-compose logs -f ipfs
```

View service status:
```bash
docker-compose ps
```

## Troubleshooting

1. Signal Linking Issues:
- Ensure you use the latest Signal mobile app
- Try refreshing the QR code by restarting the service
- Check signal-api logs for any errors

2. IPFS Connectivity:
```bash
# Connect to public IPFS nodes if needed
docker-compose exec ipfs ipfs swarm connect /dns4/ipfs.io/tcp/4001/p2p/QmSoLer265NRgSp2LA3dPaeykiS1J6DifTC88f5uVQKNAd
```

3. Bot Issues:
```bash
# Check bot logs
docker-compose logs bot

# Verify database
sqlite3 downloads/pins.db "SELECT * FROM pins;"

# Check downloaded files
ls -l downloads/
```

4. Reset Everything:
```bash
docker-compose down
rm -rf downloads/* signal-cli-config/* ipfs_data/*
docker-compose up -d
```

## Contributing

Feel free to open issues or submit pull requests for improvements.

# ONVIF Virtual Camera Service

An application that transforms video files into RTSP streams with ONVIF support, enabling seamless integration with NVR platforms such as UniFi Protect.

![Demo](static/demo.gif)

## ✨ Key Features

- 📹 Convert video files into looping RTSP streams
- 🔌 Provide independent ONVIF Device/Media services for each camera
- 🌐 Web UI management interface
- 🔄 Automatic camera configuration save and restore
- 🚀 Support multiple cameras running simultaneously

---

## 🖥️ System Requirements

### Required Dependencies

#### 1. **Python 3.8+** (3.13+ recommended)

Python is required for running the service. After installing uv, you can use it to manage Python environments.

```bash
# Check if Python is available
python3 --version

# If Python is not installed:
# Ubuntu/Debian:
sudo apt update && sudo apt install -y python3 python3-pip

# macOS:
brew install python3
```

#### 2. **uv** (Python package/venv manager)
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

#### 3. **FFmpeg** (for video to RTSP streaming)
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Verify installation
ffmpeg -version
```

#### 4. **mediamtx** (RTSP server)

> **Note**: Choose the appropriate version for your system from [mediamtx releases](https://github.com/bluenviron/mediamtx/releases/tag/v1.15.5)

```bash
# Download mediamtx (choose one based on your system)
# Linux AMD64:
wget -O mediamtx.tar.gz https://github.com/bluenviron/mediamtx/releases/download/v1.15.5/mediamtx_v1.15.5_linux_amd64.tar.gz

# macOS Apple Silicon (M1/M2/M3):
wget -O mediamtx.tar.gz https://github.com/bluenviron/mediamtx/releases/download/v1.15.5/mediamtx_v1.15.5_darwin_arm64.tar.gz

# macOS Intel:
wget -O mediamtx.tar.gz https://github.com/bluenviron/mediamtx/releases/download/v1.15.5/mediamtx_v1.15.5_darwin_amd64.tar.gz

# Extract and install
tar -xzf mediamtx.tar.gz
sudo mv mediamtx /usr/local/bin/
sudo chmod +x /usr/local/bin/mediamtx
rm mediamtx.tar.gz

# Verify installation
mediamtx --version
```



---

## 📦 Installation & Deployment & Start Service

### 🐳 Method 1: Docker Deployment (Easiest - Recommended)

**Only requires Docker installed!** No need to install Python, FFmpeg, or mediamtx.

```bash
# 1. Enter project directory
cd mock-onvif-camera

# 2. Start all services with one command
docker compose up -d

# 3. Open Web UI
open http://localhost:9999
```

**⚠️ Important Note about Docker Networking (or Port Forwarding Environment):**

Due to Docker's bridge network, the Web UI will display the container's internal IP (172.x.x.x) by default. To show your **host machine's IP address** instead, set the `SERVER_IP` environment variable:

```bash
# Find your host IP first
ifconfig | grep "inet " | grep -v 127.0.0.1

# Start with your host IP (example: 192.168.1.100)
SERVER_IP=192.168.1.100 docker compose up -d
```


**Docker Port Mapping:**
- Port `9999`: Web management interface
- Port `8554`: RTSP streaming (mediamtx)
- Ports `12000-12999`: ONVIF services (one port per camera, supports up to 1000 cameras)

### Method 2: Automated Deployment (Native)

```bash
# 1. Enter project directory
cd mock-onvif-camera

# 2. Run automated deployment script
./setup.sh

# 3. One-click startup (automatically starts mediamtx and main service)
./start.sh
```

### Method 3: Manual Installation (Native)

```bash
# 1. Enter project directory
cd mock-onvif-camera

# 2. Create virtual environment using uv
uv venv

# 3. Install Python dependencies using uv
uv pip install -r requirements.txt

# 4. Create necessary directories
mkdir -p data/videos data/cameras data/snapshots logs/onvif logs/ffmpeg static

# 5-1. Start up mediamtx
mediamtx

# 5-2. Start up service
cd mockingCameras
.venv/bin/python3 run.py
```

---

## 📝 Usage

### Create New Camera

1. Click "UPLOAD VIDEO" in Web UI
2. Select video file and configure settings:
    - **Camera Count**: 
      - `Single Camera`: Create one camera instance
      - `Multi-Cameras`: Create multiple cameras sharing the same video (batch mode)
    - **Quality Settings**:
      - Choose preset: `480p`, `720p`, `1080p`, `4K`, `5K`
      - Or use `Custom` to set resolution, FPS, and bitrate manually
    - **Enable Sub Profile (480p)**: Optional secondary 480p stream for single cameras
      - Main profile uses your selected quality
      - Sub profile fixed at 480p (accessible via ONVIF Profile_2)
3. System will automatically:
    - Start FFmpeg process to push video to RTSP
    - Start independent ONVIF server instance
    - Assign unique port and ID

### View Camera Information

Each camera displays:
- **Camera ID**: Unique identifier
- **RTSP URL**: `rtsp://your-ip:8554/[camera-id]`
- **ONVIF URL**: `http://your-ip:[port]/onvif/device_service`
- **Port**: Each camera has independent ONVIF port (12000, 12001, 12002...)
- **Authentication**: Username `test` with any password

### Delete Camera

Click the "TERMINATE" button on camera card, will automatically:
- Stop FFmpeg process
- Stop ONVIF server
- Delete video and configuration files

---

## 🏗️ System Architecture

```
Video File Upload
   ↓
Pre-transcoding (H.264/AAC optimization)
   ↓
Snapshot Generation (thumbnail filter, 100 frames analysis)
   ↓
FFmpeg (stream copy, push to mediamtx)
   ↓
mediamtx:8554 (RTSP server)
   ↓
NVR Platform (pull stream directly)
   ↑
ONVIF Server:12000+ (provide device info and stream URL)
```

### Process Flow

1. **Video Upload**: User uploads video file via Web UI
2. **Pre-transcoding**: FFmpeg transcodes video to optimized H.264/AAC format for efficient streaming
3. **Snapshot Generation**: FFmpeg analyzes first 100 frames (after skipping 2 seconds) using thumbnail filter to select the best representative frame
4. **FFmpeg Streaming**: Reads transcoded video, uses stream copy mode (no re-encoding), loops playback, pushes RTSP stream to mediamtx
5. **mediamtx**: Receives FFmpeg's stream, serves as RTSP server externally
6. **ONVIF Server**: Provides ONVIF Device/Media services

---

## 🔍 Verifying Streaming

### Test RTSP Stream
```bash
# After creating camera, test RTSP stream
ffprobe rtsp://localhost:8554/[camera-id]

# Or play with VLC
vlc rtsp://localhost:8554/[camera-id]
```

### Test ONVIF Service
```bash
# ONVIF GetSystemDateAndTime (no auth required per ONVIF spec)
curl -X POST http://localhost:12000/onvif/device_service \
  -H "Content-Type: application/soap+xml; charset=utf-8" \
  -d '<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <soap:Body>
    <tds:GetSystemDateAndTime/>
  </soap:Body>
</soap:Envelope>'

# ONVIF GetDeviceInformation (requires auth)
curl -u test:pass -X POST http://localhost:12000/onvif/device_service \
  -H "Content-Type: application/soap+xml" \
  -d '<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <soap:Body>
    <tds:GetDeviceInformation/>
  </soap:Body>
</soap:Envelope>'
```

### 4. List All Cameras
```bash
curl -s http://localhost:9999/cameras | jq
```

---

## 🛠️ Performance Optimization

### Two-Stage Processing

The system uses a two-stage approach to minimize CPU usage during streaming:

#### Stage 1: Pre-transcoding (On Upload)
Videos are transcoded once during upload to an optimized format:

```bash
ffmpeg -i input.mp4 \
  -c:v libx264 \
  -preset medium \             # Better compression (one-time cost)
  -profile:v baseline \
  -level 3.1 \
  -pix_fmt yuv420p \
  -b:v 2M \                    # Target bitrate 2Mbps
  -maxrate 2.5M \
  -bufsize 4M \
  -g 30 \                      # Keyframe every second
  -c:a aac -b:a 128k -ar 16000 -ac 1 \
  output_transcoded.mp4
```

#### Stage 2: Stream Copy (During Streaming)
FFmpeg uses stream copy mode (no re-encoding) for minimal CPU usage:

```bash
ffmpeg -re -stream_loop -1 -i transcoded.mp4 \
  -c:v copy \                  # No video re-encoding
  -c:a copy \                  # No audio re-encoding
  -f rtsp rtsp://127.0.0.1:8554/[camera-id]
```

### Snapshot Generation

Snapshots are generated using FFmpeg's thumbnail filter for optimal quality:

```bash
ffmpeg -ss 00:00:02 -i video.mp4 \
  -vf thumbnail=100 \          # Analyze 100 frames, select best
  -frames:v 1 \
  -q:v 2 \                     # High quality JPEG
  snapshot.jpg
```

**Note**: Recommended to upload videos longer than 5 seconds for best snapshot quality (skips first 2 seconds to avoid black screens/titles).

### CPU Usage

- **Expected CPU usage during streaming**: ~1-2% per camera (stream copy mode)
- **Expected CPU usage during upload**: ~30-50% (one-time transcoding)
- **Snapshot generation time**: ~1-2 seconds per video

---

## 📂 Directory Structure

```
mock-onvif-service/
├── .venv/                  # Python virtual environment (created by uv)
├── app/                    # Application modules
│   ├── __init__.py         # Package initializer
│   ├── app.py              # Flask main application
│   ├── camera_manager.py   # Camera management logic
│   ├── startup.py          # Dependency service startup
│   ├── onvif_instance.py   # ONVIF instance management
│   └── utils.py            # Utility functions
├── static/                 # Frontend assets
│   ├── index.html          # Web UI
│   ├── app.js              # Frontend logic
│   └── styles.css          # Styles
├── data/                   # Runtime data (created on first run)
│   ├── videos/             # Uploaded video storage
│   ├── snapshots/          # Camera snapshots/thumbnails (JPEG)
│   └── cameras/            # Camera configuration files (YAML)
├── onvif_server.py         # ONVIF server implementation
├── run.py                  # Main startup script
├── requirements.txt        # Python dependencies
├── mediamtx.yml            # mediamtx configuration (macOS only)
├── setup.sh                # Automated setup script (cross-platform)
├── start.sh                # Quick start script (cross-platform)
└── README.md
```

### Key Files

- **`setup.sh`**: One-time setup script that installs dependencies and creates environment
- **`start.sh`**: Starts mediamtx and MockCamera service
- **`run.py`**: Python entry point that initializes Flask app and restores cameras
- **`onvif_server.py`**: Standalone ONVIF server process for each camera
- **`mediamtx.yml`**: Configuration for mediamtx (used on macOS, not on Linux)

### Generated Directories

These directories are automatically created when you run the service:

- **`data/videos/`**: Stores uploaded video files (named by camera UUID)
- **`data/snapshots/`**: Stores camera snapshots/thumbnails extracted from videos (first frame)
- **`data/cameras/`**: Stores camera configuration YAML files
- **`logs/ffmpeg/`**: Logs from FFmpeg streaming processes
- **`logs/onvif/`**: Logs from ONVIF server instances
- **`.venv/`**: Python virtual environment managed by uv

---

## 📊 Port Usage

| Service | Port | Purpose |
|---------|------|---------|
| Flask Web UI | 9999 | Web management interface |
| mediamtx RTSP | 8554 | RTSP streaming service |
| ONVIF Camera 1 | 12000 | First camera's ONVIF service |
| ONVIF Camera 2 | 12001 | Second camera's ONVIF service |
| ONVIF Camera N | 12000+N-1 | Nth camera's ONVIF service |

---
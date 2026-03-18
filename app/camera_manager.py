import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import yaml

from app.constants import EXTEND_FRAME_DURATION
from app.log_manager import LogManager
from app.utils import get_server_ip, is_port_in_use

# Setup application logger
app_logger = logging.getLogger('camera_manager')
app_logger.setLevel(logging.INFO)
if not app_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    app_logger.addHandler(handler)

# Port configuration
MEDIAMTX_HOST = os.getenv('MEDIAMTX_HOST', '127.0.0.1')  # MediaMTX hostname (Docker: 'mediamtx', Local: '127.0.0.1')
MEDIAMTX_RTSP_PORT = int(os.getenv('MEDIAMTX_PORT', '8554'))  # Fixed RTSP port for mediamtx
ONVIF_PORT_MIN = 12000     # ONVIF port range start
ONVIF_PORT_MAX = 13000     # ONVIF port range end

# Data directories
DATA_DIR = Path("./data")
VIDEOS_DIR = DATA_DIR / "videos"
CAMERAS_DIR = DATA_DIR / "cameras"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

# Logs directory (separate from data)
LOGS_DIR = Path("./logs")
FFMPEG_LOGS_DIR = LOGS_DIR / "ffmpeg"
ONVIF_LOGS_DIR = LOGS_DIR / "onvif"

# In-memory registry
CAMERAS = {}

# Store log threads for cleanup
LOG_THREADS = {}

# Locks for thread-safe operations
PORT_ALLOCATION_LOCK = Lock()
CAMERAS_DICT_LOCK = Lock()


def build_atempo_chain(speed):
    """Build atempo filter chain for audio speed adjustment.

    atempo only supports 0.5-2.0 range, so we chain multiple filters for higher/lower speeds.

    Args:
        speed: Target speed multiplier (e.g., 2.0 for 2x speed)

    Returns:
        list: List of atempo filter strings
    """
    if speed == 1.0:
        return []

    filters = []
    remaining = speed

    # For speeds > 2.0, chain multiple atempo=2.0
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0

    # For speeds < 0.5, chain multiple atempo=0.5
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5

    # Apply remaining adjustment if not 1.0
    if abs(remaining - 1.0) > 0.001:
        filters.append(f"atempo={remaining:.4f}")

    return filters


def apply_freeze_frame(video_path, duration=EXTEND_FRAME_DURATION):
    """Apply freeze frame effect to the end of a video.

    Runs a separate FFmpeg command to extend the last frame with tpad.
    This is done separately to avoid filter chain conflicts.

    Args:
        video_path: Path to the video file
        duration: Duration in seconds to freeze the last frame

    Returns:
        Path: Path to the processed video (same as input, modified in place)

    Raises:
        Exception: If FFmpeg fails
    """
    video_path = Path(video_path)
    temp_path = video_path.with_suffix('.temp.mp4')

    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", f"tpad=stop_mode=clone:stop_duration={duration}",
        "-af", f"apad=pad_dur={duration}",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-c:a", "aac",
        "-y",
        str(temp_path)
    ]

    try:
        app_logger.info(f"Applying freeze frame ({duration}s) to: {video_path}")
        app_logger.info(f"FFmpeg freeze command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            app_logger.error(f"FFmpeg freeze stderr: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

        # Replace original with processed file
        temp_path.replace(video_path)
        app_logger.info(f"Freeze frame applied successfully: {video_path}")
        return video_path
    except subprocess.CalledProcessError as e:
        # Clean up temp file on failure
        if temp_path.exists():
            temp_path.unlink()
        app_logger.error(f"Failed to apply freeze frame: {e.stderr}")
        raise Exception(f"Failed to apply freeze frame: {e.stderr}")


def build_edit_description(trim_start, trim_duration, speed, extend_last_frame):
    """Build a human-readable description of video edit operations for logging.

    Args:
        trim_start: Start time in seconds
        trim_duration: Duration of trimmed segment (or None)
        speed: Playback speed multiplier
        extend_last_frame: Whether last frame extension is enabled

    Returns:
        str: Description string like " (trim:0-30s, speed:2x)" or empty string
    """
    parts = []
    if trim_duration:
        parts.append(f"trim:{trim_start}-{trim_start + trim_duration}s")
    if speed != 1.0:
        parts.append(f"speed:{speed}x")
    if extend_last_frame:
        parts.append(f"extend:+{EXTEND_FRAME_DURATION}s")
    if parts:
        return f" ({', '.join(parts)})"
    return ""


class CameraManager:

    @staticmethod
    def _extract_onvif_params(video_params):
        """Extract ONVIF parameters from video_params

        Args:
            video_params: Video parameters dict {'width', 'height', 'fps', 'video_bitrate', 'audio_bitrate'}

        Returns:
            tuple: (width, height, fps, video_bitrate_kbps, audio_bitrate_kbps)
        """
        width = video_params['width']
        height = video_params['height']
        fps = video_params['fps']
        # Convert bitrate from '2.5M' format to Kbps
        video_bitrate_kbps = int(float(video_params['video_bitrate'].rstrip('M')) * 1024)
        # Convert bitrate from '128k' format to Kbps integer
        audio_bitrate_kbps = int(video_params['audio_bitrate'].rstrip('k'))

        return width, height, fps, video_bitrate_kbps, audio_bitrate_kbps

    @staticmethod
    def transcode_video(input_path, output_path, video_params, sub_profile=False, edit_params=None):
        """Pre-transcode video to optimized format for low-CPU streaming

        Args:
            input_path: Path to original video file
            output_path: Path to save transcoded video (main stream)
            video_params: Video parameters dict {'width', 'height', 'fps', 'video_bitrate', 'audio_bitrate'}
            sub_profile: If True, also generate a 480p sub-stream file
            edit_params: Optional dict with edit parameters:
                - trim_start: Start time in seconds
                - trim_end: End time in seconds (None for no trim)
                - speed: Playback speed multiplier (0.5-4.0)
                - extend_last_frame: Boolean to add 10s freeze frame at end

        Returns:
            str or tuple: output_path if single profile, (output_path, output_path_sub) if sub_profile

        Raises:
            Exception: If transcoding fails or invalid params
        """
        try:
            width = int(video_params['width'])
            height = int(video_params['height'])
            fps = float(video_params['fps'])
            video_bitrate = video_params['video_bitrate']  # e.g., '2.5M'
            audio_bitrate = video_params['audio_bitrate']  # e.g., '128k'

            preset_config = {
                'resolution': f'{width}x{height}',
                'fps': fps,
                'video_bitrate': video_bitrate,
                'video_maxrate': f"{float(video_bitrate.rstrip('M')) * 1.2}M",
                'video_bufsize': f"{float(video_bitrate.rstrip('M')) * 2}M",
                'gop': int(round(fps)),  # GOP must be integer, round fps value
                'audio_bitrate': audio_bitrate,
                'description': f'{width}x{height}'
            }
        except (KeyError, ValueError, TypeError) as e:
            raise Exception(f"Invalid video parameters: {str(e)}")

        # Extract edit parameters
        trim_start = 0
        trim_duration = None
        speed = 1.0
        extend_last_frame = False

        if edit_params:
            trim_start = edit_params.get('trim_start', 0)
            trim_end = edit_params.get('trim_end')
            speed = edit_params.get('speed', 1.0)
            extend_last_frame = edit_params.get('extend_last_frame', False)

            if trim_end and trim_end > trim_start:
                trim_duration = trim_end - trim_start

        # Build video and audio filter chains (without freeze frame - handled separately)
        video_filters = [f"scale={preset_config['resolution']}"]
        audio_filters = []

        # Speed adjustment
        if speed != 1.0:
            # Video: setpts=PTS/speed (e.g., 2x speed = setpts=0.5*PTS)
            video_filters.append(f"setpts={1/speed}*PTS")
            # Audio: atempo chain
            audio_filters.extend(build_atempo_chain(speed))

        # Build filter strings
        vf_main = ','.join(video_filters)
        af_main = ','.join(audio_filters) if audio_filters else None

        # Build input options for trimming
        input_opts = []
        if trim_start > 0:
            input_opts.extend(["-ss", str(trim_start)])
        if trim_duration:
            input_opts.extend(["-t", str(trim_duration)])

        if not sub_profile:
            # Single profile mode
            cmd = ["ffmpeg"]

            # Add input options (trimming)
            cmd.extend(input_opts)
            cmd.extend(["-i", str(input_path)])

            # Video encoding with preset configuration
            cmd.extend([
                "-vf", vf_main,
                "-c:v", "libx264",
                "-preset", "medium",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-pix_fmt", "yuv420p",
                "-b:v", preset_config['video_bitrate'],
                "-maxrate", preset_config['video_maxrate'],
                "-bufsize", preset_config['video_bufsize'],
                "-g", str(preset_config['gop']),
                "-keyint_min", str(preset_config['gop']),
                "-sc_threshold", "0",
                "-r", str(preset_config['fps']),
            ])

            # Audio encoding with optional filters
            if af_main:
                cmd.extend(["-af", af_main])
            cmd.extend([
                "-c:a", "aac",
                "-b:a", preset_config['audio_bitrate'],
                "-ar", "16000",
                "-ac", "1",
                "-profile:a", "aac_low",
            ])

            cmd.extend(["-y", str(output_path)])

            try:
                edit_desc = build_edit_description(trim_start, trim_duration, speed, extend_last_frame) if edit_params else ""
                app_logger.info(f"Transcoding video: {preset_config['description']}{edit_desc}")
                app_logger.info(f"FFmpeg command: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    app_logger.error(f"FFmpeg stderr: {result.stderr}")
                    raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)
                app_logger.info(f"Video transcoded successfully: {output_path}")

                # Apply freeze frame in separate pass if requested
                if extend_last_frame:
                    apply_freeze_frame(output_path)

                return output_path
            except subprocess.CalledProcessError as e:
                app_logger.error(f"Failed to transcode video: {e.stderr}")
                raise Exception(f"Failed to transcode video: {e.stderr}")
        else:
            # Sub-profile mode: first generate main stream, then downscale to 360p
            # Step 1: Generate main profile (reuse single profile logic)
            cmd = ["ffmpeg"]
            cmd.extend(input_opts)
            cmd.extend(["-i", str(input_path)])
            cmd.extend([
                "-vf", vf_main,
                "-c:v", "libx264",
                "-preset", "medium",
                "-profile:v", "baseline",
                "-level", "3.1",
                "-pix_fmt", "yuv420p",
                "-b:v", preset_config['video_bitrate'],
                "-maxrate", preset_config['video_maxrate'],
                "-bufsize", preset_config['video_bufsize'],
                "-g", str(preset_config['gop']),
                "-keyint_min", str(preset_config['gop']),
                "-sc_threshold", "0",
                "-r", str(preset_config['fps']),
            ])
            if af_main:
                cmd.extend(["-af", af_main])
            cmd.extend([
                "-c:a", "aac",
                "-b:a", preset_config['audio_bitrate'],
                "-ar", "16000",
                "-ac", "1",
                "-profile:a", "aac_low",
                "-y",
                str(output_path),
            ])

            try:
                edit_desc = build_edit_description(trim_start, trim_duration, speed, extend_last_frame) if edit_params else ""
                app_logger.info(f"Transcoding main profile: {preset_config['description']}{edit_desc}")
                app_logger.info(f"FFmpeg command: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    app_logger.error(f"FFmpeg stderr: {result.stderr}")
                    raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

                # Apply freeze frame to main profile if requested
                if extend_last_frame:
                    apply_freeze_frame(output_path)

                app_logger.info(f"Main profile transcoded: {output_path}")

                # Step 2: Downscale main profile to 360p sub-stream
                aspect_ratio = width / height
                sub_height = 360
                dynamic_sub_width = int(round(sub_height * aspect_ratio / 2) * 2)
                sub_resolution = f"{dynamic_sub_width}x{sub_height}"

                output_path_sub = Path(str(output_path).replace('.mp4', '_sub.mp4'))

                cmd_sub = [
                    "ffmpeg",
                    "-i", str(output_path),
                    "-vf", f"scale={sub_resolution}",
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-profile:v", "baseline",
                    "-level", "3.1",
                    "-pix_fmt", "yuv420p",
                    "-b:v", "0.75M",
                    "-maxrate", "1M",
                    "-bufsize", "1.5M",
                    "-g", "24",
                    "-keyint_min", "24",
                    "-sc_threshold", "0",
                    "-r", "24",
                    "-c:a", "aac",
                    "-b:a", "64k",
                    "-ar", "16000",
                    "-ac", "1",
                    "-profile:a", "aac_low",
                    "-y",
                    str(output_path_sub)
                ]

                app_logger.info(f"Generating 360p sub-profile: {sub_resolution}")
                app_logger.info(f"FFmpeg command: {' '.join(cmd_sub)}")
                result_sub = subprocess.run(cmd_sub, capture_output=True, text=True)
                if result_sub.returncode != 0:
                    app_logger.error(f"FFmpeg sub stderr: {result_sub.stderr}")
                    raise subprocess.CalledProcessError(result_sub.returncode, cmd_sub, result_sub.stdout, result_sub.stderr)

                app_logger.info(f"Sub-profile transcoded: {output_path_sub}")
                return output_path, output_path_sub

            except subprocess.CalledProcessError as e:
                app_logger.error(f"Failed to transcode video: {e.stderr}")
                raise Exception(f"Failed to transcode video: {e.stderr}")

    @staticmethod
    def generate_snapshot(video_path, camera_id):
        """Generate snapshot from video file using FFmpeg

        Args:
            video_path: Path to video file
            camera_id: Camera ID for snapshot filename

        Returns:
            Path: Path to generated snapshot file

        Raises:
            Exception: If FFmpeg fails to generate snapshot
        """
        SNAPSHOTS_DIR.mkdir(exist_ok=True)
        snapshot_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"

        # FFmpeg command to extract best frame using thumbnail filter
        # Scale down to max 720p (1280x720) while maintaining aspect ratio, don't upscale
        cmd = [
            "ffmpeg",
            "-ss", "00:00:02",         # Skip first 2 seconds (avoid black screens/titles)
            "-i", str(video_path),
            "-vf", "thumbnail=100,scale='min(iw,1280)':'min(ih,720)':force_original_aspect_ratio=decrease",
            "-frames:v", "1",          # Extract 1 frame
            "-q:v", "2",               # High quality JPEG (2-5 is good, 2 is best)
            "-y",                      # Overwrite if exists
            str(snapshot_path)
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True, text=True)
            return snapshot_path
        except subprocess.CalledProcessError as e:
            app_logger.error(f"Failed to generate snapshot: {e.stderr}")
            raise Exception(f"Failed to generate snapshot: {e.stderr}")

    @staticmethod
    def log_ffmpeg_output(process, logger, camera_id):
        """
        Read FFmpeg output and write to rotating log
        Runs in a separate thread
        """
        try:
            for line in process.stdout:
                if line:
                    logger.info(line.rstrip())
        except Exception as e:
            logger.error(f"Error reading FFmpeg output: {e}")
        finally:
            # Clean up thread reference
            if camera_id in LOG_THREADS:
                del LOG_THREADS[camera_id]

    @staticmethod
    def get_ffmpeg_command(video_path, camera_id):
        """Generate FFmpeg command for streaming video to RTSP

        Args:
            video_path: Path to video file
            camera_id: Camera ID for RTSP stream path

        Returns:
            List of command arguments for subprocess
        """
        return [
            "ffmpeg",
            "-re",  # Read input at native frame rate
            "-stream_loop", "-1",  # Loop infinitely
            "-i", str(video_path),

            # Use copy mode - video is already transcoded
            "-c:v", "copy",
            "-c:a", "copy",

            # RTSP output
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/{camera_id}"
        ]

    @staticmethod
    def start_ffmpeg_process(video_path, camera_id):
        """Start FFmpeg process for streaming video to RTSP

        Args:
            video_path: Path to video file (can be main or sub stream)
            camera_id: Camera ID for RTSP stream

        Returns:
            int: FFmpeg process PID

        Raises:
            Exception: If FFmpeg fails to start
        """
        ffmpeg_cmd = CameraManager.get_ffmpeg_command(video_path, camera_id)

        # Create log file with rotation
        FFMPEG_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = FFMPEG_LOGS_DIR / f"ffmpeg_{camera_id[:8]}.log"

        # Create rotating logger (3MB max, 3 backups)
        logger, _ = LogManager.create_rotating_logger(log_file)

        # Start FFmpeg process with logging
        ffmpeg_process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            universal_newlines=True,
            bufsize=1
        )
        ffmpeg_pid = ffmpeg_process.pid

        # Start log reading thread
        log_thread = threading.Thread(
            target=CameraManager.log_ffmpeg_output,
            args=(ffmpeg_process, logger, camera_id),
            daemon=True
        )
        log_thread.start()
        LOG_THREADS[camera_id] = log_thread

        app_logger.info(f"FFmpeg started for camera {camera_id[:8]} (PID: {ffmpeg_pid})")

        return ffmpeg_pid

    @staticmethod
    def start_onvif_server(camera_id, onvif_port, width, height, fps, video_bitrate_kbps, audio_bitrate_kbps, shared_video_id=None, sub_profile=False, camera_name='MockONVIF'):
        """Start ONVIF server instance for a camera

        Args:
            camera_id: Camera ID
            onvif_port: Port for ONVIF server
            width: Video width in pixels
            height: Video height in pixels
            fps: Frames per second
            video_bitrate_kbps: Video bitrate in Kbps
            audio_bitrate_kbps: Audio bitrate in Kbps
            shared_video_id: Shared video ID (for batch cameras, used for snapshot)
            sub_profile: Enable sub-profile (480p) in ONVIF response
            camera_name: Manufacturer name for the camera (default: 'MockONVIF')

        Returns:
            int: ONVIF server process PID

        Raises:
            Exception: If ONVIF server fails to start
        """
        server_ip = get_server_ip()
        rtsp_url = f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}"

        # Get paths
        onvif_server_path = os.path.join(os.getcwd(), 'onvif_server.py')
        venv_python = os.path.join(os.getcwd(), '.venv', 'bin', 'python3')

        # Fallback to system python if venv not found
        if not os.path.exists(venv_python):
            venv_python = 'python3'

        # Setup environment with direct parameters
        onvif_env = os.environ.copy()
        env_vars = {
            'ONVIF_CAMERA_ID': camera_id,
            'ONVIF_RTSP_URL': rtsp_url,
            'ONVIF_PORT': str(onvif_port),
            'ONVIF_SERVER_IP': server_ip,
            'ONVIF_WIDTH': str(width),
            'ONVIF_HEIGHT': str(height),
            'ONVIF_FPS': str(fps),
            'ONVIF_VIDEO_BITRATE_KBPS': str(video_bitrate_kbps),
            'ONVIF_AUDIO_BITRATE_KBPS': str(audio_bitrate_kbps),
            'ONVIF_SUB_PROFILE': 'true' if sub_profile else 'false',
            'ONVIF_MANUFACTURER': camera_name,
        }

        # Add shared_video_id if provided (for batch cameras)
        if shared_video_id:
            env_vars['ONVIF_SHARED_VIDEO_ID'] = shared_video_id

        onvif_env.update(env_vars)

        # Create log file with rotation
        ONVIF_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = ONVIF_LOGS_DIR / f"onvif_{camera_id[:8]}.log"

        # Create rotating logger (3MB max, 3 backups)
        logger, _ = LogManager.create_rotating_logger(log_file)

        # Start ONVIF server
        onvif_process = subprocess.Popen(
            [venv_python, onvif_server_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=onvif_env,
            cwd=os.getcwd(),
            start_new_session=True,
            universal_newlines=True,
            bufsize=1
        )
        onvif_pid = onvif_process.pid

        # Start log reading thread
        log_thread = threading.Thread(
            target=CameraManager.log_ffmpeg_output,  # Reuse same logging function
            args=(onvif_process, logger, f"onvif_{camera_id}"),
            daemon=True
        )
        log_thread.start()
        LOG_THREADS[f"onvif_{camera_id}"] = log_thread

        # Give it a moment to start
        time.sleep(1)

        # Check if it's still running
        if onvif_process.poll() is not None:
            # Read from logger instead
            time.sleep(0.5)  # Give logger time to write
            try:
                with open(log_file, 'r') as log_f:
                    error_log = log_f.read()
            except Exception:
                error_log = "Unable to read log file"
            app_logger.error(f"ONVIF server failed to start for camera {camera_id[:8]}")
            raise Exception(f"ONVIF server failed to start. Log: {error_log[:200]}")

        app_logger.info(f"ONVIF server started for camera {camera_id[:8]} on port {onvif_port} (PID: {onvif_pid})")

        return onvif_pid

    @staticmethod
    def cleanup_defunct_processes():
        """Clean up any defunct (zombie) processes from terminated cameras"""
        try:
            # Use waitpid with -1 to reap any defunct child processes
            while True:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                    if pid == 0:
                        break  # No more defunct processes
                    app_logger.debug(f"Reaped defunct process (PID: {pid})")
                except ChildProcessError:
                    break  # No more children
        except Exception:
            pass

    @staticmethod
    def allocate_port(port_min, port_max, used_ports):
        """Allocate an available port from the given range (thread-safe)"""
        with PORT_ALLOCATION_LOCK:
            for port in range(port_min, port_max):
                if port not in used_ports and not is_port_in_use(port):
                    used_ports.add(port)  # Mark as used immediately
                    return port
            raise Exception("No available ports in range")

    @staticmethod
    def get_used_onvif_ports():
        """Get all ONVIF ports currently in use by cameras (thread-safe)"""
        with CAMERAS_DICT_LOCK:
            onvif_ports = set()
            for camera in CAMERAS.values():
                onvif_ports.add(camera['onvif_port'])
            return onvif_ports

    @staticmethod
    def list_cameras():
        """List all cameras"""
        return list(CAMERAS.values())

    @staticmethod
    def _create_single_camera_instance(camera_id, final_video_path, shared_video_id, video_params, onvif_used_ports, skip_snapshot=False, sub_profile=False, camera_name='MockONVIF'):
        """Create a single camera instance (used for parallel processing)

        Args:
            camera_id: Unique camera ID
            final_video_path: Path to the shared transcoded video
            shared_video_id: ID of the shared video file
            video_params: Video parameters dict {'width', 'height', 'fps', 'video_bitrate', 'audio_bitrate'}
            onvif_used_ports: Set of already allocated ports (thread-safe)
            skip_snapshot: If True, skip snapshot generation (use shared snapshot)
            sub_profile: Enable sub-profile (480p) stream
            camera_name: Manufacturer name for the camera (default: 'MockONVIF')

        Returns:
            tuple: (camera_info dict, error message or None)
        """
        try:
            # Generate snapshot for this camera (only if not skipped)
            if not skip_snapshot:
                try:
                    CameraManager.generate_snapshot(final_video_path, camera_id)
                except Exception as e:
                    app_logger.warning(f"Failed to generate snapshot for camera {camera_id[:8]}: {str(e)}")

            # Allocate ONVIF port (thread-safe)
            try:
                onvif_port = CameraManager.allocate_port(ONVIF_PORT_MIN, ONVIF_PORT_MAX, onvif_used_ports)
            except Exception as e:
                return None, f"Failed to allocate ONVIF port: {str(e)}"

            # Start FFmpeg process (using the shared transcoded video)
            ffmpeg_pid_sub = None
            try:
                ffmpeg_pid = CameraManager.start_ffmpeg_process(final_video_path, camera_id)

                # Start second FFmpeg process for sub-stream if sub_profile enabled
                if sub_profile:
                    final_video_path_sub = Path(str(final_video_path).replace('.mp4', '_sub.mp4'))
                    ffmpeg_pid_sub = CameraManager.start_ffmpeg_process(
                        final_video_path_sub, f"{camera_id}_sub")
            except Exception as e:
                if ffmpeg_pid:
                    try:
                        os.kill(ffmpeg_pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                return None, f"Failed to start FFmpeg: {str(e)}"

            # Save config for reference
            config_path = CAMERAS_DIR / f"config_{camera_id}.yaml"
            created_at = int(time.time())
            config = {
                'camera_id': camera_id,
                'rtsp_stream': f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/{camera_id}",
                'onvif_port': onvif_port,
                'video_params': video_params,
                'shared_video_id': shared_video_id,
                'sub_profile': sub_profile,
                'manufacturer': camera_name,
                'created_at': created_at,
            }

            try:
                with open(config_path, 'w') as f:
                    yaml.dump(config, f)
            except Exception as e:
                os.kill(ffmpeg_pid, signal.SIGTERM)
                return None, f"Failed to create config: {str(e)}"

            # Extract ONVIF parameters and start dedicated ONVIF server instance
            try:
                width, height, fps, video_bitrate_kbps, audio_bitrate_kbps = CameraManager._extract_onvif_params(video_params)
                onvif_pid = CameraManager.start_onvif_server(
                    camera_id, onvif_port, width, height, fps, video_bitrate_kbps, audio_bitrate_kbps, shared_video_id, sub_profile, camera_name)
            except Exception as e:
                os.kill(ffmpeg_pid, signal.SIGTERM)
                if config_path.exists():
                    os.remove(config_path)
                return None, f"Failed to start ONVIF server: {str(e)}"

            # Create camera record
            server_ip = get_server_ip()
            camera_info = {
                "id": camera_id,
                "video_path": str(final_video_path),
                "rtsp_port": MEDIAMTX_RTSP_PORT,
                "onvif_port": onvif_port,
                "ffmpeg_pid": ffmpeg_pid,
                "onvif_pid": onvif_pid,
                "rtsp_url": f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}",
                "onvif_url": f"{server_ip}:{onvif_port}",
                "username": "test",
                "password": "pass",
                "shared_video_id": shared_video_id,
                "width": width,
                "height": height,
                "fps": fps,
                "video_bitrate_mbps": round(video_bitrate_kbps / 1000, 2),
                "sub_profile": sub_profile,
                "manufacturer": camera_name,
                "created_at": created_at
            }

            # Add sub-stream info if sub_profile enabled
            if sub_profile:
                camera_info["ffmpeg_pid_sub"] = ffmpeg_pid_sub
                camera_info["rtsp_url_sub"] = f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}_sub"

            # Add to global CAMERAS dict (thread-safe)
            with CAMERAS_DICT_LOCK:
                CAMERAS[camera_id] = camera_info

            return camera_info, None

        except Exception as e:
            return None, f"Unexpected error: {str(e)}"

    @staticmethod
    def create_cameras_batch(video_file, video_params, count=50, sub_profile=False, camera_name='MockONVIF', edit_params=None):
        """Create multiple fake ONVIF cameras from a single uploaded video

        This method transcodes the video once and creates multiple camera instances
        that all use the same transcoded video file.

        Args:
            video_file: Uploaded video file
            video_params: Video parameters dict {'width', 'height', 'fps', 'video_bitrate', 'audio_bitrate'}
            count: Number of cameras to create
            sub_profile: Enable sub-profile (480p) stream
            camera_name: Manufacturer name for the camera (default: 'MockONVIF')
            edit_params: Optional dict with edit parameters (trim_start, trim_end, speed, extend_last_frame)

        Returns:
            List of camera info dictionaries
        """
        app_logger.info(f"Starting batch camera deployment: {count} cameras with {video_params['width']}x{video_params['height']}")

        # Generate a shared video ID for the transcoded file
        shared_video_id = str(uuid.uuid4())

        # Step 1: Save video file (temporary)
        temp_video_path = VIDEOS_DIR / f"{shared_video_id}_temp.mp4"
        final_video_path = VIDEOS_DIR / f"{shared_video_id}_shared.mp4"
        try:
            video_file.save(str(temp_video_path))
        except Exception as e:
            app_logger.error(f"Failed to save video: {str(e)}")
            raise Exception(f"Failed to save video: {str(e)}")

        # Step 2: Transcode video ONCE to optimized format (and 480p if sub_profile)
        final_video_path_sub = None
        try:
            result = CameraManager.transcode_video(
                temp_video_path, final_video_path, video_params, sub_profile, edit_params)
            if sub_profile:
                final_video_path, final_video_path_sub = result
            # Delete original video after successful transcode
            os.remove(temp_video_path)
        except Exception as e:
            # Clean up on transcode failure
            if temp_video_path.exists():
                os.remove(temp_video_path)
            if final_video_path.exists():
                os.remove(final_video_path)
            if final_video_path_sub and Path(final_video_path_sub).exists():
                os.remove(final_video_path_sub)
            raise Exception(f"Failed to transcode video: {str(e)}")

        # Step 3: Generate a single shared snapshot for all cameras in this batch
        try:
            CameraManager.generate_snapshot(final_video_path, shared_video_id)
            app_logger.info("Generated shared snapshot for batch")
        except Exception as e:
            app_logger.warning(f"Failed to generate shared snapshot: {str(e)}")

        # Step 4: Create multiple cameras in parallel using the same video
        camera_infos = []
        failed_count = 0

        app_logger.info(f"Creating {count} camera instances in parallel (max {min(20, count)} workers)...")

        # Pre-allocate camera IDs
        camera_ids = [str(uuid.uuid4()) for _ in range(count)]

        # Shared set for port allocation (thread-safe via lock)
        onvif_used_ports = CameraManager.get_used_onvif_ports()

        # Use ThreadPoolExecutor for parallel processing
        # Limit workers to avoid overwhelming the system
        max_workers = min(20, count)  # Max 20 parallel workers

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all camera creation tasks (skip individual snapshots)
            future_to_index = {
                executor.submit(
                    CameraManager._create_single_camera_instance,
                    camera_id,
                    final_video_path,
                    shared_video_id,
                    video_params,
                    onvif_used_ports,
                    True,  # skip_snapshot=True, use shared snapshot
                    sub_profile,
                    camera_name
                ): i for i, camera_id in enumerate(camera_ids)
            }

            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_index):
                completed += 1
                index = future_to_index[future]

                try:
                    camera_info, error = future.result()
                    if camera_info:
                        camera_infos.append(camera_info)
                    else:
                        failed_count += 1
                        if error:
                            app_logger.warning(f"Camera {index+1} failed: {error}")
                except Exception as e:
                    failed_count += 1
                    app_logger.error(f"Camera {index+1} exception: {str(e)}")

                # Log progress every 20 cameras
                if completed % 20 == 0 or completed == count:
                    app_logger.info(
                        f"Progress: {completed}/{count} cameras processed ({len(camera_infos)} succeeded, {failed_count} failed)")

        app_logger.info(f"Batch deployment complete: {len(camera_infos)}/{count} cameras created successfully")
        if failed_count > 0:
            app_logger.warning(f"{failed_count} cameras failed to create")

        return camera_infos

    @staticmethod
    def create_camera(video_file, video_params, sub_profile=False, camera_name='MockONVIF', edit_params=None):
        """Create a new fake ONVIF camera from uploaded video

        Args:
            video_file: Uploaded video file
            video_params: Video parameters dict {'width', 'height', 'fps', 'video_bitrate', 'audio_bitrate'}
            sub_profile: Enable sub-profile (480p) stream
            camera_name: Manufacturer name for the camera (default: 'MockONVIF')
            edit_params: Optional dict with edit parameters (trim_start, trim_end, speed, extend_last_frame)
        """
        camera_id = str(uuid.uuid4())

        # Step 1: Save video file (temporary)
        temp_video_path = VIDEOS_DIR / f"{camera_id}_temp.mp4"
        final_video_path = VIDEOS_DIR / f"{camera_id}.mp4"
        try:
            video_file.save(str(temp_video_path))
        except Exception as e:
            raise Exception(f"Failed to save video: {str(e)}")

        # Step 2: Transcode video to optimized format with specified parameters (and 480p if sub_profile)
        final_video_path_sub = None
        try:
            app_logger.info(f"Transcoding video for camera {camera_id[:8]}...")
            result = CameraManager.transcode_video(
                temp_video_path, final_video_path, video_params, sub_profile, edit_params)
            if sub_profile:
                final_video_path, final_video_path_sub = result
            # Delete original video after successful transcode
            os.remove(temp_video_path)
        except Exception as e:
            # Clean up on transcode failure
            if temp_video_path.exists():
                os.remove(temp_video_path)
            if final_video_path.exists():
                os.remove(final_video_path)
            if final_video_path_sub and Path(final_video_path_sub).exists():
                os.remove(final_video_path_sub)
            raise Exception(f"Failed to transcode video: {str(e)}")

        # Step 3: Generate snapshot from transcoded video
        try:
            CameraManager.generate_snapshot(final_video_path, camera_id)
        except Exception as e:
            os.remove(final_video_path)
            raise Exception(f"Failed to generate snapshot: {str(e)}")

        # Step 4: Allocate ONVIF port
        onvif_used = CameraManager.get_used_onvif_ports()
        try:
            onvif_port = CameraManager.allocate_port(ONVIF_PORT_MIN, ONVIF_PORT_MAX, onvif_used)
        except Exception as e:
            # Clean up video and snapshot
            os.remove(final_video_path)
            snapshot_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"
            if snapshot_path.exists():
                os.remove(snapshot_path)
            raise Exception(f"Failed to allocate ONVIF port: {str(e)}")

        # Step 5: Start FFmpeg → mediamtx RTSP server (using copy mode)
        ffmpeg_pid_sub = None
        try:
            ffmpeg_pid = CameraManager.start_ffmpeg_process(final_video_path, camera_id)

            # Start second FFmpeg process for sub-stream if sub_profile enabled
            if sub_profile and final_video_path_sub:
                ffmpeg_pid_sub = CameraManager.start_ffmpeg_process(
                    final_video_path_sub, f"{camera_id}_sub")
        except Exception as e:
            # Clean up video and snapshot
            os.remove(final_video_path)
            snapshot_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"
            if snapshot_path.exists():
                os.remove(snapshot_path)
            raise Exception(f"Failed to start FFmpeg: {str(e)}")

        # Step 6: Save config for reference
        config_path = CAMERAS_DIR / f"config_{camera_id}.yaml"
        created_at = int(time.time())

        config = {
            'camera_id': camera_id,
            'rtsp_stream': f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/{camera_id}",
            'onvif_port': onvif_port,
            'video_params': video_params,
            'sub_profile': sub_profile,
            'manufacturer': camera_name,
            'created_at': created_at,
        }

        try:
            with open(config_path, 'w') as f:
                yaml.dump(config, f)
        except Exception as e:
            os.kill(ffmpeg_pid, signal.SIGTERM)
            os.remove(final_video_path)
            snapshot_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"
            if snapshot_path.exists():
                os.remove(snapshot_path)
            raise Exception(f"Failed to create config: {str(e)}")

        # Step 7: Extract ONVIF parameters and start dedicated ONVIF server instance for this camera
        try:
            width, height, fps, video_bitrate_kbps, audio_bitrate_kbps = CameraManager._extract_onvif_params(video_params)
            onvif_pid = CameraManager.start_onvif_server(
                camera_id, onvif_port, width, height, fps, video_bitrate_kbps, audio_bitrate_kbps, None, sub_profile, camera_name)
        except Exception as e:
            os.kill(ffmpeg_pid, signal.SIGTERM)
            os.remove(final_video_path)
            os.remove(config_path)
            snapshot_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"
            if snapshot_path.exists():
                os.remove(snapshot_path)
            raise Exception(f"Failed to start ONVIF server: {str(e)}")

        # Step 8: Create camera record
        server_ip = get_server_ip()
        camera_info = {
            "id": camera_id,
            "video_path": str(final_video_path),
            "rtsp_port": MEDIAMTX_RTSP_PORT,
            "onvif_port": onvif_port,
            "ffmpeg_pid": ffmpeg_pid,
            "onvif_pid": onvif_pid,
            "rtsp_url": f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}",
            "onvif_url": f"{server_ip}:{onvif_port}",
            "username": "test",
            "password": "pass",
            "width": width,
            "height": height,
            "fps": fps,
            "video_bitrate_mbps": round(video_bitrate_kbps / 1000, 2),
            "sub_profile": sub_profile,
            "manufacturer": camera_name,
            "created_at": created_at
        }

        # Add sub-stream info if sub_profile enabled
        if sub_profile:
            camera_info["ffmpeg_pid_sub"] = ffmpeg_pid_sub
            camera_info["rtsp_url_sub"] = f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}_sub"

        CAMERAS[camera_id] = camera_info

        return camera_info

    @staticmethod
    def delete_camera(camera_id):
        """Delete a fake ONVIF camera"""
        if camera_id not in CAMERAS:
            raise Exception("Camera not found")

        camera = CAMERAS[camera_id]
        app_logger.info(f"Deleting camera {camera_id[:8]}")

        # Step 1: Terminate FFmpeg process(es) and reap defunct process
        try:
            ffmpeg_pid = camera['ffmpeg_pid']

            # Try to kill the main FFmpeg process
            try:
                os.kill(ffmpeg_pid, signal.SIGTERM)
                time.sleep(0.5)

                # Check if still running, force kill if needed
                try:
                    os.kill(ffmpeg_pid, 0)  # Check if process exists
                    os.kill(ffmpeg_pid, signal.SIGKILL)
                    time.sleep(0.2)
                except ProcessLookupError:
                    pass  # Process terminated

            except ProcessLookupError:
                pass  # Process already dead

            # Reap defunct process using waitpid with WNOHANG
            try:
                pid, _ = os.waitpid(ffmpeg_pid, os.WNOHANG)
                if pid == 0:
                    # Process not yet defunct, try one more time
                    time.sleep(0.3)
                    os.waitpid(ffmpeg_pid, os.WNOHANG)
            except ChildProcessError:
                # Not a child process or already reaped
                pass
            except Exception as e:
                app_logger.debug(f"Could not reap defunct process: {e}")

            # Terminate sub-stream FFmpeg process if exists
            if 'ffmpeg_pid_sub' in camera and camera['ffmpeg_pid_sub']:
                ffmpeg_pid_sub = camera['ffmpeg_pid_sub']
                try:
                    os.kill(ffmpeg_pid_sub, signal.SIGTERM)
                    time.sleep(0.5)

                    try:
                        os.kill(ffmpeg_pid_sub, 0)
                        os.kill(ffmpeg_pid_sub, signal.SIGKILL)
                        time.sleep(0.2)
                    except ProcessLookupError:
                        pass
                except ProcessLookupError:
                    pass

                # Reap sub-stream process
                try:
                    pid, _ = os.waitpid(ffmpeg_pid_sub, os.WNOHANG)
                    if pid == 0:
                        time.sleep(0.3)
                        os.waitpid(ffmpeg_pid_sub, os.WNOHANG)
                except (ChildProcessError, Exception) as e:
                    app_logger.debug(f"Could not reap sub-stream defunct process: {e}")

            app_logger.info(f"Stopped FFmpeg process (PID: {ffmpeg_pid})")

        except ProcessLookupError:
            app_logger.debug(f"FFmpeg process (PID: {camera.get('ffmpeg_pid')}) already terminated")
        except Exception as e:
            app_logger.warning(f"Failed to kill FFmpeg process: {str(e)}")

        # Step 1.5: Kill ONVIF server process
        if 'onvif_pid' in camera and camera['onvif_pid']:
            try:
                # Kill the process group to ensure all child processes are terminated
                os.killpg(os.getpgid(camera['onvif_pid']), signal.SIGTERM)
                app_logger.info(f"Stopped ONVIF server (PID: {camera['onvif_pid']})")
            except (ProcessLookupError, PermissionError):
                pass  # Process already dead or no permission
            except Exception as e:
                app_logger.warning(f"Failed to kill ONVIF process: {str(e)}")

        # Step 2: Delete video file (only if no other cameras are using it)
        try:
            video_path = Path(camera['video_path'])
            if video_path.exists():
                # Check if this is a shared video
                shared_video_id = camera.get('shared_video_id')
                if shared_video_id:
                    # Count how many other cameras are using this video
                    cameras_using_video = sum(
                        1 for cam in CAMERAS.values()
                        if cam.get('shared_video_id') == shared_video_id and cam['id'] != camera_id
                    )

                    if cameras_using_video > 0:
                        app_logger.debug(f"Video file retained ({cameras_using_video} other camera(s) still using it)")
                    else:
                        os.remove(video_path)
                        app_logger.info("Deleted shared video file")
                else:
                    # Not a shared video, safe to delete
                    os.remove(video_path)
                    app_logger.info("Deleted video file")
        except Exception as e:
            app_logger.warning(f"Failed to delete video file: {str(e)}")

        # Step 2.5: Delete snapshot file
        try:
            shared_video_id = camera.get('shared_video_id')
            if shared_video_id:
                # For batch cameras, check if we should delete the shared snapshot
                cameras_using_snapshot = sum(
                    1 for cam in CAMERAS.values()
                    if cam.get('shared_video_id') == shared_video_id and cam['id'] != camera_id
                )

                if cameras_using_snapshot == 0:
                    # Last camera using this snapshot, delete the shared snapshot
                    snapshot_path = SNAPSHOTS_DIR / f"{shared_video_id}.jpg"
                    if snapshot_path.exists():
                        os.remove(snapshot_path)
                        app_logger.info("Deleted shared snapshot")
            else:
                # Individual camera, delete its own snapshot
                snapshot_path = SNAPSHOTS_DIR / f"{camera_id}.jpg"
                if snapshot_path.exists():
                    os.remove(snapshot_path)
        except Exception as e:
            app_logger.warning(f"Failed to delete snapshot file: {str(e)}")

        # Step 3: Delete config file
        try:
            config_path = CAMERAS_DIR / f"config_{camera_id}.yaml"
            if config_path.exists():
                os.remove(config_path)
        except Exception as e:
            app_logger.warning(f"Failed to delete config file: {str(e)}")

        # Step 4: Remove from registry
        del CAMERAS[camera_id]

        return {"status": "deleted", "id": camera_id}

    @staticmethod
    def _restore_single_camera(config_path, onvif_used_ports):
        """Restore a single camera (used for parallel processing)

        Args:
            config_path: Path to the camera config file
            onvif_used_ports: Set of already allocated ports (thread-safe)

        Returns:
            tuple: (camera_info dict, error message or None)
        """
        try:
            # Read config to get camera_id and video path info
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)

            camera_id = config.get('camera_id')
            if not camera_id:
                return None, f"{config_path.name}: no camera_id in config"

            # Determine video path based on whether it's a batch camera
            shared_video_id = config.get('shared_video_id')
            if shared_video_id:
                # Batch camera - use shared video file
                video_path = VIDEOS_DIR / f"{shared_video_id}_shared.mp4"
            else:
                # Single camera - use individual video file
                video_path = VIDEOS_DIR / f"{camera_id}.mp4"

            # Check if video file exists
            if not video_path.exists():
                return None, f"{camera_id[:8]}: video file not found: {video_path.name}"

            # Check and generate snapshot if missing
            # For batch cameras, use shared snapshot; for single cameras, use individual snapshot
            snapshot_id = shared_video_id if shared_video_id else camera_id
            snapshot_path = SNAPSHOTS_DIR / f"{snapshot_id}.jpg"
            if not snapshot_path.exists():
                try:
                    CameraManager.generate_snapshot(video_path, snapshot_id)
                except Exception as e:
                    app_logger.warning(f"Failed to generate snapshot for {camera_id[:8]}: {str(e)}")

            # Get sub_profile setting from config
            sub_profile = config.get('sub_profile', False)

            # Start FFmpeg process(es)
            ffmpeg_pid_sub = None
            try:
                ffmpeg_pid = CameraManager.start_ffmpeg_process(video_path, camera_id)

                # Start second FFmpeg process for sub-stream if sub_profile enabled
                if sub_profile:
                    video_path_sub = Path(str(video_path).replace('.mp4', '_sub.mp4'))
                    if video_path_sub.exists():
                        ffmpeg_pid_sub = CameraManager.start_ffmpeg_process(
                            video_path_sub, f"{camera_id}_sub")
                    else:
                        app_logger.warning(f"Sub-profile enabled but sub video file not found: {video_path_sub}")
                        sub_profile = False  # Disable sub_profile if file missing
            except Exception as e:
                if ffmpeg_pid:
                    try:
                        os.kill(ffmpeg_pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        pass
                return None, f"{camera_id[:8]}: Failed to start FFmpeg: {str(e)}"

            # Try to restore original ONVIF port, or allocate new one if not available
            original_port = config.get('onvif_port')
            onvif_port = None
            port_changed = False

            if original_port:
                # Try to use original port (thread-safe check)
                with PORT_ALLOCATION_LOCK:
                    if original_port not in onvif_used_ports and not is_port_in_use(original_port):
                        # Use original port if available
                        onvif_port = original_port
                        onvif_used_ports.add(onvif_port)

            if not onvif_port:
                # Original port not available or in use, allocate new port
                try:
                    onvif_port = CameraManager.allocate_port(ONVIF_PORT_MIN, ONVIF_PORT_MAX, onvif_used_ports)
                    port_changed = True
                except Exception as e:
                    # Clean up FFmpeg
                    try:
                        os.kill(ffmpeg_pid, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                    return None, f"{camera_id[:8]}: Failed to allocate ONVIF port: {str(e)}"

            # Update config if port changed
            if port_changed:
                try:
                    config['onvif_port'] = onvif_port
                    with open(config_path, 'w') as f:
                        yaml.dump(config, f)
                except Exception as e:
                    app_logger.warning(f"Failed to update config with new port for {camera_id[:8]}: {str(e)}")

            # Get video_params from config (required)
            video_params = config.get('video_params')
            if not video_params:
                return None, f"{camera_id[:8]}: Missing video_params in config. Run migrate_configs.py first."

            # Get manufacturer from config
            manufacturer = config.get('manufacturer', 'MockONVIF')

            # Get created_at from config (default to 0 for old configs)
            created_at = config.get('created_at', 0)

            # Extract ONVIF parameters and start ONVIF server
            try:
                width, height, fps, video_bitrate_kbps, audio_bitrate_kbps = CameraManager._extract_onvif_params(video_params)
                onvif_pid = CameraManager.start_onvif_server(
                    camera_id, onvif_port, width, height, fps, video_bitrate_kbps, audio_bitrate_kbps, shared_video_id, sub_profile, manufacturer)
            except Exception as e:
                # ONVIF server failed, clean up FFmpeg process(es)
                try:
                    os.kill(ffmpeg_pid, signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                if ffmpeg_pid_sub:
                    try:
                        os.kill(ffmpeg_pid_sub, signal.SIGTERM)
                    except (ProcessLookupError, OSError):
                        pass
                return None, f"{camera_id[:8]}: Failed to start ONVIF server: {str(e)}"

            # Create camera info
            server_ip = get_server_ip()
            camera_info = {
                "id": camera_id,
                "video_path": str(video_path),
                "rtsp_port": MEDIAMTX_RTSP_PORT,
                "onvif_port": onvif_port,
                "ffmpeg_pid": ffmpeg_pid,
                "onvif_pid": onvif_pid,
                "rtsp_url": f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}",
                "onvif_url": f"{server_ip}:{onvif_port}",
                "username": "test",
                "password": "pass",
                "width": width,
                "height": height,
                "fps": fps,
                "video_bitrate_mbps": round(video_bitrate_kbps / 1000, 2),
                "sub_profile": sub_profile,
                "manufacturer": manufacturer,
                "created_at": created_at
            }

            # Add shared_video_id for batch cameras
            if shared_video_id:
                camera_info["shared_video_id"] = shared_video_id
            
            # Add sub-stream info if sub_profile enabled
            if sub_profile:
                camera_info["ffmpeg_pid_sub"] = ffmpeg_pid_sub
                camera_info["rtsp_url_sub"] = f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{camera_id}_sub"

            # Add to global CAMERAS dict (thread-safe)
            with CAMERAS_DICT_LOCK:
                CAMERAS[camera_id] = camera_info

            return camera_info, None

        except Exception as e:
            return None, f"Unexpected error: {str(e)}"

    @staticmethod
    def restore_cameras():
        """Restore cameras from existing video and config files on startup (parallel processing)"""
        # First, clean up any defunct processes
        CameraManager.cleanup_defunct_processes()

        app_logger.info("Restoring previous cameras...")

        # Scan cameras directory for existing config files
        if not CAMERAS_DIR.exists():
            app_logger.info("No cameras directory found, skipping restoration")
            return

        config_files = list(CAMERAS_DIR.glob("config_*.yaml"))

        if not config_files:
            app_logger.info("No existing cameras found")
            return

        total_count = len(config_files)
        app_logger.info(f"Found {total_count} camera config(s), attempting to restore in parallel...")

        # Shared set for port allocation (thread-safe via lock)
        onvif_used_ports = CameraManager.get_used_onvif_ports()

        # Use ThreadPoolExecutor for parallel processing
        max_workers = min(20, total_count)  # Max 20 parallel workers
        app_logger.info(f"Starting parallel restore with {max_workers} workers...")

        restored_cameras = []
        failed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all restore tasks
            future_to_config = {
                executor.submit(
                    CameraManager._restore_single_camera,
                    config_path,
                    onvif_used_ports
                ): config_path for config_path in config_files
            }

            # Collect results as they complete
            completed = 0
            for future in as_completed(future_to_config):
                completed += 1
                config_path = future_to_config[future]

                try:
                    camera_info, error = future.result()
                    if camera_info:
                        restored_cameras.append(camera_info)
                    else:
                        failed_count += 1
                        if error:
                            app_logger.warning(f"Restore failed: {error}")
                except Exception as e:
                    failed_count += 1
                    app_logger.error(f"Restore exception for {config_path.name}: {str(e)}")

                # Log progress every 20 cameras or at completion
                if completed % 20 == 0 or completed == total_count:
                    app_logger.info(
                        f"Progress: {completed}/{total_count} cameras processed ({len(restored_cameras)} succeeded, {failed_count} failed)")

        app_logger.info(f"Restoration complete: {len(restored_cameras)}/{total_count} cameras active")

        if failed_count > 0:
            app_logger.warning(f"{failed_count} cameras failed to restore")

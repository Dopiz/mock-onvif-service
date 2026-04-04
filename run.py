#!/usr/bin/env python3
"""
Fake ONVIF Camera Service - Startup Script
"""

import atexit
import os
import signal
import sys

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def cleanup_on_exit():
    """Clean up all resources when server stops"""
    import time

    print("\n" + "="*60)
    print(" SHUTTING DOWN SERVICE...")
    print("="*60)

    try:
        # Stop log cleanup scheduler (with timeout)
        from app.log_cleanup_scheduler import stop_log_cleanup_scheduler
        stop_log_cleanup_scheduler()
    except Exception as e:
        print(f"  ⚠ Error stopping log cleanup scheduler: {e}")

    try:
        from app.camera_manager import CAMERAS

        # Get list of all camera IDs
        camera_ids = list(CAMERAS.keys())

        if camera_ids:
            print(f"\nCleaning up {len(camera_ids)} camera(s)...")
            for camera_id in camera_ids:
                try:
                    camera = CAMERAS[camera_id]
                    camera_short_id = camera_id[:8]

                    # Kill FFmpeg with timeout
                    if 'ffmpeg_pid' in camera:
                        try:
                            pid = camera['ffmpeg_pid']
                            os.kill(pid, signal.SIGTERM)
                            # Wait briefly, then force kill if still running
                            time.sleep(0.5)
                            try:
                                os.kill(pid, 0)  # Check if process exists
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass  # Process already terminated
                            print(f"  ✓ Stopped FFmpeg (PID: {pid}) - Camera {camera_short_id}...")
                        except ProcessLookupError:
                            pass

                    # Kill ONVIF server with timeout
                    if 'onvif_pid' in camera and camera['onvif_pid']:
                        try:
                            pid = camera['onvif_pid']
                            os.killpg(os.getpgid(pid), signal.SIGTERM)
                            # Wait briefly, then force kill if still running
                            time.sleep(0.5)
                            try:
                                os.kill(pid, 0)  # Check if process exists
                                os.killpg(os.getpgid(pid), signal.SIGKILL)
                            except (ProcessLookupError, OSError):
                                pass  # Process already terminated
                            print(f"  ✓ Stopped ONVIF server (PID: {pid}) - Camera {camera_short_id}...")
                        except (ProcessLookupError, PermissionError, OSError):
                            pass

                except Exception as e:
                    print(f"  ⚠ Error cleaning up camera {camera_id[:8]}...: {e}")

        print("\n✓ All resources cleaned up")

        # macvlan: clean up all cam_* interfaces
        try:
            from app.camera_manager import MACVLAN_ENABLED, _get_macvlan_manager
            if MACVLAN_ENABLED:
                _get_macvlan_manager().cleanup_all()
                print("  ✓ Cleaned up macvlan interfaces")
        except Exception as e:
            print(f"  ⚠ Error cleaning up macvlan interfaces: {e}")

    except Exception as e:
        print(f"\n⚠ Error during cleanup: {e}")

    print("="*60 + "\n")


def signal_handler(signum, frame):
    """Handle interrupt signals"""
    print("\n\nReceived termination signal, shutting down...")
    cleanup_on_exit()
    sys.exit(0)


def main():
    # Register cleanup handlers
    atexit.register(cleanup_on_exit)
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill command

    # Ensure data directories exist
    os.makedirs('data/videos', exist_ok=True)
    os.makedirs('data/cameras', exist_ok=True)
    os.makedirs('static', exist_ok=True)

    from app.app import app
    from app.camera_manager import CameraManager
    from app.startup import startup_dependencies

    startup_dependencies()
    CameraManager.restore_cameras()

    print("\n --- \n")

    # Get configuration from environment variables
    host = os.getenv('SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('SERVER_PORT', '9999'))
    debug = os.getenv('DEBUG_MODE', 'false').lower() in ('true', '1', 'yes')

    print(f"Starting server on {host}:{port} (debug={debug})")

    try:
        app.run(host=host, port=port, debug=debug, use_reloader=False)
    except KeyboardInterrupt:
        pass  # cleanup_on_exit will be called by atexit


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Fake ONVIF Camera Service - Startup Script
"""

import atexit
import os
import signal
import sys


def cleanup_on_exit():
    """Clean up all resources when server stops"""
    print("\n" + "="*60)
    print(" SHUTTING DOWN SERVICE...")
    print("="*60)

    try:
        # Stop log cleanup scheduler
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

                    # Kill FFmpeg
                    if 'ffmpeg_pid' in camera:
                        try:
                            os.kill(camera['ffmpeg_pid'], signal.SIGTERM)
                            print(f"  ✓ Stopped FFmpeg (PID: {camera['ffmpeg_pid']}) - Camera {camera_short_id}...")
                        except ProcessLookupError:
                            pass

                    # Kill ONVIF server
                    if 'onvif_pid' in camera and camera['onvif_pid']:
                        try:
                            os.killpg(os.getpgid(camera['onvif_pid']), signal.SIGTERM)
                            print(
                                f"  ✓ Stopped ONVIF server (PID: {camera['onvif_pid']}) - Camera {camera_short_id}...")
                        except (ProcessLookupError, PermissionError, OSError):
                            pass

                except Exception as e:
                    print(f"  ⚠ Error cleaning up camera {camera_id[:8]}...: {e}")

        print("\n✓ All resources cleaned up")

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

    try:
        app.run(host='0.0.0.0', port=9999, debug=True, use_reloader=False)
    except KeyboardInterrupt:
        pass  # cleanup_on_exit will be called by atexit


if __name__ == '__main__':
    main()

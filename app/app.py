import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from app.camera_manager import CameraManager
from app.constants import (
    CUSTOM_PARAM_RANGES,
    EDIT_LIMITS,
    EXTEND_FRAME_DURATION,
    VALID_AUDIO_BITRATES,
)

app = Flask(__name__, static_folder='../static', static_url_path='')
CORS(app)


@app.route('/')
def index():
    """Serve the frontend"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/upload', methods=['POST'])
def upload_video():
    """Upload video and create fake ONVIF camera(s) with specific video parameters"""
    if 'file' not in request.files:
        return jsonify({"error": "no file"}), 400

    video_file = request.files['file']

    if video_file.filename == '':
        return jsonify({"error": "no file"}), 400

    # Get camera count from form data, default to 1
    camera_count = int(request.form.get('camera_count', '1'))

    # Get sub_profile from form data, default to False
    sub_profile = request.form.get('sub_profile', 'false').lower() == 'true'

    # Get camera name (manufacturer) from form data, default to 'MockONVIF'
    camera_name = request.form.get('camera_name', 'MockONVIF').strip() or 'MockONVIF'

    # Get specific video parameters (frontend calculates these based on aspect ratio)
    try:
        width = int(request.form.get('width', 1920))
        height = int(request.form.get('height', 1080))
        fps = float(request.form.get('fps', 30))
        video_bitrate = request.form.get('video_bitrate', '4M')
        audio_bitrate = request.form.get('audio_bitrate', '128k')

        # Validate parameters using constants
        width_range = CUSTOM_PARAM_RANGES['width']
        height_range = CUSTOM_PARAM_RANGES['height']
        fps_range = CUSTOM_PARAM_RANGES['fps']
        bitrate_range = CUSTOM_PARAM_RANGES['video_bitrate_mbps']

        if not (width_range['min'] <= width <= width_range['max']):
            return jsonify({"error": f"Width must be between {width_range['min']} and {width_range['max']}"}), 400
        if not (height_range['min'] <= height <= height_range['max']):
            return jsonify({"error": f"Height must be between {height_range['min']} and {height_range['max']}"}), 400
        if not (fps_range['min'] <= fps <= fps_range['max']):
            return jsonify({"error": f"FPS must be between {fps_range['min']} and {fps_range['max']}"}), 400

        # Round fps to 1 decimal place
        fps = round(fps, 1)

        # Validate video bitrate format (e.g., "2.5M")
        try:
            bitrate_value = float(video_bitrate.rstrip('M'))
            if not (bitrate_range['min'] <= bitrate_value <= bitrate_range['max']):
                return jsonify({"error": f"Video bitrate must be between {bitrate_range['min']}M and {bitrate_range['max']}M"}), 400
        except (ValueError, AttributeError):
            return jsonify({"error": "Invalid video bitrate format. Use format like '2.5M'"}), 400

        # Validate audio bitrate
        if audio_bitrate not in VALID_AUDIO_BITRATES:
            return jsonify({"error": f"Audio bitrate must be one of: {', '.join(VALID_AUDIO_BITRATES)}"}), 400

        video_params = {
            'width': width,
            'height': height,
            'fps': fps,
            'video_bitrate': video_bitrate,
            'audio_bitrate': audio_bitrate
        }
    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid video parameters: {str(e)}"}), 400

    # Validate camera count
    if not (1 <= camera_count <= 100):
        return jsonify({"error": "Invalid camera count. Please enter a number between 1 and 100"}), 400

    # Extract edit parameters
    try:
        trim_start = float(request.form.get('trim_start', '0'))
        trim_end = float(request.form.get('trim_end', '0'))
        speed = float(request.form.get('speed', '1.0'))
        extend_last_frame = request.form.get('extend_last_frame', 'false').lower() == 'true'

        # Validate speed
        if not (EDIT_LIMITS['min_speed'] <= speed <= EDIT_LIMITS['max_speed']):
            return jsonify({"error": f"Speed must be between {EDIT_LIMITS['min_speed']}x and {EDIT_LIMITS['max_speed']}x"}), 400

        # Validate duration if trim_end is set
        if trim_end > 0:
            if trim_end <= trim_start:
                return jsonify({"error": "End time must be greater than start time"}), 400

            raw_duration = trim_end - trim_start
            output_duration = raw_duration / speed + (EXTEND_FRAME_DURATION if extend_last_frame else 0)

            if output_duration < EDIT_LIMITS['min_duration']:
                return jsonify({"error": f"Output duration must be at least {EDIT_LIMITS['min_duration']} seconds"}), 400
            if output_duration > EDIT_LIMITS['max_duration']:
                return jsonify({"error": f"Output duration cannot exceed {EDIT_LIMITS['max_duration']} seconds (3 minutes)"}), 400

        # Build edit_params dict (only if any edits are applied)
        edit_params = None
        if trim_start > 0 or trim_end > 0 or speed != 1.0 or extend_last_frame:
            edit_params = {
                'trim_start': trim_start,
                'trim_end': trim_end if trim_end > 0 else None,
                'speed': speed,
                'extend_last_frame': extend_last_frame
            }

    except (ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid edit parameters: {str(e)}"}), 400

    try:
        if camera_count == 1:
            camera_info = CameraManager.create_camera(video_file, video_params, sub_profile, camera_name, edit_params)
            return jsonify(camera_info), 201
        else:
            # Batch create cameras
            camera_infos = CameraManager.create_cameras_batch(
                video_file, video_params, camera_count, sub_profile, camera_name, edit_params)
            return jsonify({"cameras": camera_infos, "count": len(camera_infos)}), 201
    except Exception as e:
        error_msg = str(e)
        if "save video" in error_msg:
            return jsonify({"error": "save video failed"}), 500
        elif "FFmpeg" in error_msg:
            return jsonify({"error": "ffmpeg start failed"}), 500
        elif "Docker" in error_msg:
            return jsonify({"error": "onvif instance start failed"}), 500
        else:
            return jsonify({"error": error_msg}), 500


@app.route('/cameras', methods=['GET'])
def list_cameras():
    """List all fake ONVIF cameras"""
    cameras = CameraManager.list_cameras()
    return jsonify(cameras), 200


@app.route('/cameras/<camera_id>', methods=['DELETE'])
def delete_camera(camera_id):
    """Delete a fake ONVIF camera"""
    try:
        result = CameraManager.delete_camera(camera_id)
        return jsonify(result), 200
    except Exception as e:
        if "not found" in str(e):
            return jsonify({"error": "not found"}), 404
        else:
            return jsonify({"error": str(e)}), 500


@app.route('/data/<path:filename>')
def serve_data_file(filename):
    """Serve files from data directory (e.g., snapshots)"""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    return send_from_directory(data_dir, filename)


if __name__ == '__main__':
    # This block is only for direct execution (not recommended)
    # Use run.py instead for proper startup
    from dotenv import load_dotenv
    from app.startup import startup_dependencies

    load_dotenv()
    startup_dependencies()
    CameraManager.restore_cameras()
    
    host = os.getenv('SERVER_HOST', '0.0.0.0')
    port = int(os.getenv('SERVER_PORT', '9999'))
    debug = os.getenv('DEBUG_MODE', 'false').lower() in ('true', '1', 'yes')
    app.run(host=host, port=port, debug=debug)

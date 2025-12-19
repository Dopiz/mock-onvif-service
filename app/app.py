import os

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from app.camera_manager import CameraManager
from app.constants import (
    CUSTOM_PARAM_RANGES,
    VALID_AUDIO_BITRATES,
    get_all_preset_names,
    validate_preset,
)

app = Flask(__name__, static_folder='../static', static_url_path='')
CORS(app)


@app.route('/')
def index():
    """Serve the frontend"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/upload', methods=['POST'])
def upload_video():
    """Upload video and create fake ONVIF camera(s) with selected quality preset"""
    if 'file' not in request.files:
        return jsonify({"error": "no file"}), 400

    video_file = request.files['file']

    if video_file.filename == '':
        return jsonify({"error": "no file"}), 400

    # Get preset from form data, default to 1080p
    preset = request.form.get('preset', '1080p')

    # Get camera count from form data, default to 1
    camera_count = int(request.form.get('camera_count', '1'))

    # Get sub_profile from form data, default to False
    sub_profile = request.form.get('sub_profile', 'false').lower() == 'true'

    # Handle custom preset parameters
    custom_params = None
    if preset == 'custom':
        try:
            width = int(request.form.get('width', 1920))
            height = int(request.form.get('height', 1080))
            fps = float(request.form.get('fps', 30))
            video_bitrate = request.form.get('video_bitrate', '4M')
            audio_bitrate = request.form.get('audio_bitrate', '128k')

            # Validate custom parameters using constants
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

            custom_params = {
                'width': width,
                'height': height,
                'fps': fps,
                'video_bitrate': video_bitrate,
                'audio_bitrate': audio_bitrate
            }
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"Invalid custom parameters: {str(e)}"}), 400
    else:
        # Validate preset
        if not validate_preset(preset):
            valid_presets = ', '.join(get_all_preset_names())
            return jsonify({"error": f"Invalid preset: {preset}. Valid options: {valid_presets}, custom"}), 400

    # Validate camera count
    if not (1 <= camera_count <= 50):
        return jsonify({"error": "Invalid camera count. Please enter a number between 1 and 50"}), 400

    try:
        if camera_count == 1:
            camera_info = CameraManager.create_camera(video_file, preset, custom_params, sub_profile)
            return jsonify(camera_info), 201
        else:
            # Batch create cameras
            camera_infos = CameraManager.create_cameras_batch(
                video_file, preset, camera_count, custom_params, sub_profile)
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
    from app.startup import startup_dependencies

    startup_dependencies()
    CameraManager.restore_cameras()
    app.run(host='0.0.0.0', port=9999, debug=True)

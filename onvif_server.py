#!/usr/bin/env python3
"""
Simple ONVIF Server for Mock Cameras
Provides ONVIF Device Management and Media services
Supports multiple instances via environment variables
"""

import logging
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, Response, request, send_file

app = Flask(__name__)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Configuration from environment or defaults
CAMERA_ID = os.environ.get('ONVIF_CAMERA_ID', 'default')
RTSP_URL = os.environ.get('ONVIF_RTSP_URL', 'rtsp://127.0.0.1:8554/default')
SERVER_PORT = int(os.environ.get('ONVIF_PORT', '12999'))
SHARED_VIDEO_ID = os.environ.get('ONVIF_SHARED_VIDEO_ID')  # For batch cameras

# Video/Audio configuration parameters (passed directly from camera_manager)
WIDTH = int(os.environ.get('ONVIF_WIDTH', '1920'))
HEIGHT = int(os.environ.get('ONVIF_HEIGHT', '1080'))
FPS = float(os.environ.get('ONVIF_FPS', '30'))  # Support decimal fps (e.g., 29.97, 23.976)
VIDEO_BITRATE_KBPS = int(os.environ.get('ONVIF_VIDEO_BITRATE_KBPS', '4096'))  # in Kbps
AUDIO_BITRATE_KBPS = int(os.environ.get('ONVIF_AUDIO_BITRATE_KBPS', '128'))  # in Kbps

# Sub-profile configuration
SUB_PROFILE = os.environ.get('ONVIF_SUB_PROFILE', 'false').lower() == 'true'

# 480p parameters for sub-profile (from constants)
# Note: These values match QUALITY_PRESETS['480p'] in constants.py
SUB_PROFILE_WIDTH = 854
SUB_PROFILE_HEIGHT = 480
SUB_PROFILE_FPS = 24
SUB_PROFILE_BITRATE_KBPS = 1024  # 1.0 Mbps = 1024 Kbps

# Authentication credentials
# VALID_USERNAME = 'test'


def check_auth(username, password=None):
    """Check if username/password combination is valid"""
    return True
    # return username == VALID_USERNAME


def authenticate():
    """Send a 401 response that enables basic auth"""
    return Response(
        'Authentication required', 401,
        {'WWW-Authenticate': 'Basic realm="ONVIF"'})


def extract_ws_security(xml_data):
    """Extract username and password from WS-Security SOAP header"""
    try:
        root = ET.fromstring(xml_data)
        # Look for WS-Security header
        namespaces = {
            'soap': 'http://www.w3.org/2003/05/soap-envelope',
            'wsse': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'
        }

        username_elem = root.find('.//wsse:Username', namespaces)
        password_elem = root.find('.//wsse:Password', namespaces)

        if username_elem is not None and password_elem is not None:
            return username_elem.text, password_elem.text
    except Exception:
        pass
    return None, None


def requires_auth(f):
    """Decorator to require ONVIF authentication (HTTP Basic or WS-Security)"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Method 1: Try WS-Security in SOAP header (ONVIF standard)
        if request.data:
            try:
                xml_data = request.data.decode('utf-8')
                username, password = extract_ws_security(xml_data)
                if username and password and check_auth(username, password):
                    return f(*args, **kwargs)
            except Exception:
                pass

        # Method 2: Try HTTP Basic Auth (fallback)
        auth = request.authorization
        if auth and check_auth(auth.username, auth.password):
            return f(*args, **kwargs)

        # Method 3: Allow GetSystemDateAndTime without auth (ONVIF standard)
        if 'GetSystemDateAndTime' in request.data.decode('utf-8', errors='ignore'):
            return f(*args, **kwargs)

        return authenticate()
    return decorated


# ONVIF Namespaces
NAMESPACES = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope',
    'tds': 'http://www.onvif.org/ver10/device/wsdl',
    'trt': 'http://www.onvif.org/ver10/media/wsdl',
    'tt': 'http://www.onvif.org/ver10/schema',
}

# Camera Configuration
CAMERA_CONFIG = {
    'manufacturer': os.environ.get('ONVIF_MANUFACTURER', 'MockONVIF'),
    'model': f'{SERVER_PORT}-{CAMERA_ID[:8]}',
    'firmware_version': '1.0.2',
    'serial_number': CAMERA_ID,
    'hardware_id': f'dopiz-{CAMERA_ID[:8]}',
}


def create_soap_envelope(body_content):
    """Create SOAP envelope with body content"""
    envelope = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
               xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
               xmlns:tt="http://www.onvif.org/ver10/schema">
    <soap:Body>
        {body_content}
    </soap:Body>
</soap:Envelope>'''
    return envelope


def get_device_information_response():
    """GetDeviceInformation response"""
    body = f'''<tds:GetDeviceInformationResponse>
    <tds:Manufacturer>{CAMERA_CONFIG['manufacturer']}</tds:Manufacturer>
    <tds:Model>{CAMERA_CONFIG['model']}</tds:Model>
    <tds:FirmwareVersion>{CAMERA_CONFIG['firmware_version']}</tds:FirmwareVersion>
    <tds:SerialNumber>{CAMERA_CONFIG['serial_number']}</tds:SerialNumber>
    <tds:HardwareId>{CAMERA_CONFIG['hardware_id']}</tds:HardwareId>
</tds:GetDeviceInformationResponse>'''
    return create_soap_envelope(body)


def get_capabilities_response(camera_id):
    """GetCapabilities response"""
    server_ip = request.host.split(':')[0]
    port = request.host.split(':')[1] if ':' in request.host else '12000'

    body = f'''<tds:GetCapabilitiesResponse>
    <tds:Capabilities>
        <tt:Device>
            <tt:XAddr>http://{server_ip}:{port}/onvif/device_service</tt:XAddr>
        </tt:Device>
        <tt:Media>
            <tt:XAddr>http://{server_ip}:{port}/onvif/media_service</tt:XAddr>
        </tt:Media>
    </tds:Capabilities>
</tds:GetCapabilitiesResponse>'''
    return create_soap_envelope(body)


def get_profiles_response(camera_id):
    """GetProfiles response - dynamically generated based on parameters

    If SUB_PROFILE is enabled, generates two profiles:
    - Profile_1: User-selected quality (main profile)
    - Profile_2: Fixed 480p sub-profile
    """

    # Profile 1 - Main profile with user-selected parameters
    profile_1 = f"""<trt:Profiles token="Profile_1" fixed="true">
        <tt:Name>MainProfile</tt:Name>
        <tt:VideoSourceConfiguration token="VideoSource_1">
            <tt:Name>VideoSourceConfig</tt:Name>
            <tt:SourceToken>VideoSource_1</tt:SourceToken>
            <tt:Bounds x="0" y="0" width="{WIDTH}" height="{HEIGHT}"/>
        </tt:VideoSourceConfiguration>
        <tt:VideoEncoderConfiguration token="VideoEncoder_1">
            <tt:Name>VideoEncoderConfig</tt:Name>
            <tt:Encoding>H264</tt:Encoding>
            <tt:Resolution>
                <tt:Width>{WIDTH}</tt:Width>
                <tt:Height>{HEIGHT}</tt:Height>
            </tt:Resolution>
            <tt:Quality>5</tt:Quality>
            <tt:RateControl>
                <tt:FrameRateLimit>{FPS}</tt:FrameRateLimit>
                <tt:BitrateLimit>{VIDEO_BITRATE_KBPS}</tt:BitrateLimit>
            </tt:RateControl>
        </tt:VideoEncoderConfiguration>
        <tt:AudioSourceConfiguration token="AudioSource_1">
            <tt:Name>AudioSourceConfig</tt:Name>
            <tt:SourceToken>AudioSource_1</tt:SourceToken>
        </tt:AudioSourceConfiguration>
        <tt:AudioEncoderConfiguration token="AudioEncoder_1">
            <tt:Name>AudioEncoderConfig</tt:Name>
            <tt:Encoding>AAC</tt:Encoding>
            <tt:Bitrate>{AUDIO_BITRATE_KBPS}</tt:Bitrate>
            <tt:SampleRate>16000</tt:SampleRate>
            <tt:SessionTimeout>PT60S</tt:SessionTimeout>
        </tt:AudioEncoderConfiguration>
        <tt:AudioOutputConfiguration token="AudioOutput_1">
            <tt:Name>AudioOutputConfig</tt:Name>
            <tt:OutputToken>AudioOutput_1</tt:OutputToken>
        </tt:AudioOutputConfiguration>
    </trt:Profiles>"""

    # Profile 2 - Fixed 480p sub-profile (only if sub_profile enabled)
    profile_2 = ""
    if SUB_PROFILE:
        profile_2 = f"""
    <trt:Profiles token="Profile_2" fixed="true">
        <tt:Name>SubProfile_480p</tt:Name>
        <tt:VideoSourceConfiguration token="VideoSource_2">
            <tt:Name>VideoSourceConfig_Sub</tt:Name>
            <tt:SourceToken>VideoSource_1</tt:SourceToken>
            <tt:Bounds x="0" y="0" width="{SUB_PROFILE_WIDTH}" height="{SUB_PROFILE_HEIGHT}"/>
        </tt:VideoSourceConfiguration>
        <tt:VideoEncoderConfiguration token="VideoEncoder_2">
            <tt:Name>VideoEncoderConfig_Sub</tt:Name>
            <tt:Encoding>H264</tt:Encoding>
            <tt:Resolution>
                <tt:Width>{SUB_PROFILE_WIDTH}</tt:Width>
                <tt:Height>{SUB_PROFILE_HEIGHT}</tt:Height>
            </tt:Resolution>
            <tt:Quality>5</tt:Quality>
            <tt:RateControl>
                <tt:FrameRateLimit>{SUB_PROFILE_FPS}</tt:FrameRateLimit>
                <tt:BitrateLimit>{SUB_PROFILE_BITRATE_KBPS}</tt:BitrateLimit>
            </tt:RateControl>
        </tt:VideoEncoderConfiguration>
        <tt:AudioSourceConfiguration token="AudioSource_1">
            <tt:Name>AudioSourceConfig</tt:Name>
            <tt:SourceToken>AudioSource_1</tt:SourceToken>
        </tt:AudioSourceConfiguration>
        <tt:AudioEncoderConfiguration token="AudioEncoder_1">
            <tt:Name>AudioEncoderConfig</tt:Name>
            <tt:Encoding>AAC</tt:Encoding>
            <tt:Bitrate>{AUDIO_BITRATE_KBPS}</tt:Bitrate>
            <tt:SampleRate>16000</tt:SampleRate>
            <tt:SessionTimeout>PT60S</tt:SessionTimeout>
        </tt:AudioEncoderConfiguration>
        <tt:AudioOutputConfiguration token="AudioOutput_1">
            <tt:Name>AudioOutputConfig</tt:Name>
            <tt:OutputToken>AudioOutput_1</tt:OutputToken>
        </tt:AudioOutputConfiguration>
    </trt:Profiles>"""

    body = f"""<trt:GetProfilesResponse>
    {profile_1}{profile_2}
</trt:GetProfilesResponse>"""

    return create_soap_envelope(body)


def get_stream_uri_response(camera_id, profile_token=None):
    """GetStreamUri response

    Args:
        camera_id: Camera ID
        profile_token: Profile token from request (Profile_1 or Profile_2)

    Returns sub-stream URL for Profile_2 when sub_profile is enabled
    """
    rtsp_url = RTSP_URL

    # If requesting Profile_2 and sub_profile enabled, return sub-stream URL
    if SUB_PROFILE and profile_token == "Profile_2":
        # Replace camera_id with camera_id_sub in URL
        rtsp_url = RTSP_URL.replace(f"/{CAMERA_ID}", f"/{CAMERA_ID}_sub")

    body = f'''<trt:GetStreamUriResponse>
    <trt:MediaUri>
        <tt:Uri>{rtsp_url}</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT60S</tt:Timeout>
    </trt:MediaUri>
</trt:GetStreamUriResponse>'''
    return create_soap_envelope(body)


def get_audio_sources_response():
    """GetAudioSources response"""
    body = """<trt:GetAudioSourcesResponse>
    <trt:AudioSources token="AudioSource_1">
        <tt:Channels>1</tt:Channels>
    </trt:AudioSources>
</trt:GetAudioSourcesResponse>"""
    return create_soap_envelope(body)


def get_audio_source_configurations_response():
    """GetAudioSourceConfigurations response"""
    body = """<trt:GetAudioSourceConfigurationsResponse>
    <trt:Configurations token="AudioSource_1">
        <tt:Name>AudioSourceConfig</tt:Name>
        <tt:SourceToken>AudioSource_1</tt:SourceToken>
    </trt:Configurations>
</trt:GetAudioSourceConfigurationsResponse>"""
    return create_soap_envelope(body)


def get_audio_encoder_configurations_response():
    """GetAudioEncoderConfigurations response"""
    body = f"""<trt:GetAudioEncoderConfigurationsResponse>
    <trt:Configurations token="AudioEncoder_1">
        <tt:Name>AudioEncoderConfig</tt:Name>
        <tt:Encoding>AAC</tt:Encoding>
        <tt:Bitrate>{AUDIO_BITRATE_KBPS}</tt:Bitrate>
        <tt:SampleRate>16000</tt:SampleRate>
        <tt:SessionTimeout>PT60S</tt:SessionTimeout>
    </trt:Configurations>
</trt:GetAudioEncoderConfigurationsResponse>"""
    return create_soap_envelope(body)


def get_system_date_and_time_response():
    """GetSystemDateAndTime response"""
    now = datetime.now(timezone.utc)
    body = f'''<tds:GetSystemDateAndTimeResponse>
    <tds:SystemDateAndTime>
        <tt:DateTimeType>NTP</tt:DateTimeType>
        <tt:DaylightSavings>false</tt:DaylightSavings>
        <tt:TimeZone>
            <tt:TZ>UTC</tt:TZ>
        </tt:TimeZone>
        <tt:UTCDateTime>
            <tt:Time>
                <tt:Hour>{now.hour}</tt:Hour>
                <tt:Minute>{now.minute}</tt:Minute>
                <tt:Second>{now.second}</tt:Second>
            </tt:Time>
            <tt:Date>
                <tt:Year>{now.year}</tt:Year>
                <tt:Month>{now.month}</tt:Month>
                <tt:Day>{now.day}</tt:Day>
            </tt:Date>
        </tt:UTCDateTime>
    </tds:SystemDateAndTime>
</tds:GetSystemDateAndTimeResponse>'''
    return create_soap_envelope(body)


def get_services_response():
    """GetServices response - list all available ONVIF services"""
    server_ip = request.host.split(':')[0]
    port = request.host.split(':')[1] if ':' in request.host else '12000'

    body = f'''<tds:GetServicesResponse>
    <tds:Service>
        <tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>
        <tds:XAddr>http://{server_ip}:{port}/onvif/device_service</tds:XAddr>
        <tds:Version>
            <tt:Major>2</tt:Major>
            <tt:Minor>0</tt:Minor>
        </tds:Version>
    </tds:Service>
    <tds:Service>
        <tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>
        <tds:XAddr>http://{server_ip}:{port}/onvif/media_service</tds:XAddr>
        <tds:Version>
            <tt:Major>2</tt:Major>
            <tt:Minor>0</tt:Minor>
        </tds:Version>
    </tds:Service>
</tds:GetServicesResponse>'''
    return create_soap_envelope(body)


def get_video_sources_response():
    """GetVideoSources response"""
    body = f"""<trt:GetVideoSourcesResponse>
    <trt:VideoSources token="VideoSource_1">
        <tt:Framerate>{FPS}</tt:Framerate>
        <tt:Resolution>
            <tt:Width>{WIDTH}</tt:Width>
            <tt:Height>{HEIGHT}</tt:Height>
        </tt:Resolution>
    </trt:VideoSources>
</trt:GetVideoSourcesResponse>"""
    return create_soap_envelope(body)


def get_snapshot_uri_response():
    """GetSnapshotUri response"""
    server_ip = request.host.split(':')[0]
    port = request.host.split(':')[1] if ':' in request.host else '12000'

    body = f'''<trt:GetSnapshotUriResponse>
    <trt:MediaUri>
        <tt:Uri>http://{server_ip}:{port}/snapshot.jpg</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT10S</tt:Timeout>
    </trt:MediaUri>
</trt:GetSnapshotUriResponse>'''
    return create_soap_envelope(body)


def parse_soap_request(xml_data):
    """Parse SOAP request to extract action"""
    try:
        root = ET.fromstring(xml_data)
        # Find the first child of Body
        body = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
        if body is not None and len(body) > 0:
            action_element = body[0]
            action = action_element.tag.split('}')[1] if '}' in action_element.tag else action_element.tag
            return action
    except Exception:
        pass
    return None


@app.route('/onvif/device_service', methods=['POST'])
@requires_auth
def device_service():
    """ONVIF Device Management Service"""
    xml_data = request.data.decode('utf-8')
    action = parse_soap_request(xml_data)

    # Log the API call
    client_ip = request.remote_addr
    logger.info(f"[Device Service] {action} called from {client_ip}")

    if action == 'GetDeviceInformation':
        response_xml = get_device_information_response()
    elif action == 'GetCapabilities':
        response_xml = get_capabilities_response(CAMERA_ID)
    elif action == 'GetSystemDateAndTime':
        response_xml = get_system_date_and_time_response()
    elif action == 'GetServices':
        response_xml = get_services_response()
    else:
        logger.warning(f"[Device Service] Unknown action: {action}")
        response_xml = create_soap_envelope(f'<tds:{action}Response/>')

    return Response(response_xml, mimetype='application/soap+xml')


def extract_profile_token(xml_data):
    """Extract ProfileToken from SOAP request"""
    try:
        root = ET.fromstring(xml_data)
        # Look for ProfileToken element
        for elem in root.iter():
            if 'ProfileToken' in elem.tag:
                return elem.text
    except Exception:
        pass
    return None


@app.route('/onvif/media_service', methods=['POST'])
@requires_auth
def media_service():
    """ONVIF Media Service"""
    xml_data = request.data.decode('utf-8')
    action = parse_soap_request(xml_data)

    # Log the API call
    client_ip = request.remote_addr
    logger.info(f"[Media Service] {action} called from {client_ip}")

    if action == 'GetProfiles':
        response_xml = get_profiles_response(CAMERA_ID)
    elif action == 'GetStreamUri':
        # Extract profile token to determine which stream URL to return
        profile_token = extract_profile_token(xml_data)
        response_xml = get_stream_uri_response(CAMERA_ID, profile_token)
    elif action == 'GetSnapshotUri':
        response_xml = get_snapshot_uri_response()
    elif action == 'GetVideoSources':
        response_xml = get_video_sources_response()
    elif action == 'GetAudioSources':
        response_xml = get_audio_sources_response()
    elif action == 'GetAudioSourceConfigurations':
        response_xml = get_audio_source_configurations_response()
    elif action == 'GetAudioEncoderConfigurations':
        response_xml = get_audio_encoder_configurations_response()
    else:
        logger.warning(f"[Media Service] Unknown action: {action}")
        response_xml = create_soap_envelope(f'<trt:{action}Response/>')

    return Response(response_xml, mimetype='application/soap+xml')


@app.route('/onvif/device_service.wsdl', methods=['GET'])
@app.route('/onvif/media_service.wsdl', methods=['GET'])
def wsdl():
    """Serve WSDL (simplified)"""
    return Response('<?xml version="1.0" encoding="UTF-8"?><definitions/>', mimetype='text/xml')


@app.route('/snapshot.jpg', methods=['GET'])
def snapshot():
    """Serve camera snapshot (no auth required)

    For batch cameras, uses shared snapshot (SHARED_VIDEO_ID.jpg)
    For individual cameras, uses camera-specific snapshot (CAMERA_ID.jpg)
    """
    client_ip = request.remote_addr
    logger.info(f"[HTTP] Snapshot requested from {client_ip}")

    # Try shared snapshot first (for batch cameras)
    if SHARED_VIDEO_ID:
        shared_snapshot_path = Path(f"./data/snapshots/{SHARED_VIDEO_ID}.jpg")
        if shared_snapshot_path.exists():
            logger.info(f"[HTTP] Shared snapshot served successfully to {client_ip}")
            return send_file(str(shared_snapshot_path), mimetype='image/jpeg')
        else:
            logger.warning(f"[HTTP] Shared snapshot not found: {SHARED_VIDEO_ID}")

    # Fallback to camera-specific snapshot
    snapshot_path = Path(f"./data/snapshots/{CAMERA_ID}.jpg")
    if snapshot_path.exists():
        logger.info(f"[HTTP] Camera snapshot served successfully to {client_ip}")
        return send_file(str(snapshot_path), mimetype='image/jpeg')
    else:
        logger.warning(f"[HTTP] Snapshot not available for camera {CAMERA_ID[:8]}")
        return Response("Snapshot not available", status=404)


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return {'status': 'ok', 'service': 'ONVIF Mock Server'}


def run_server():
    """Run the ONVIF server"""
    print("\n" + "="*60, flush=True)
    print(f" ONVIF MOCK SERVER - Camera {CAMERA_ID[:8]}...", flush=True)
    print("="*60, flush=True)
    print(f"\n✓ Starting ONVIF service on port {SERVER_PORT}...", flush=True)
    print(f"✓ Camera ID: {CAMERA_ID}", flush=True)
    print(f"✓ RTSP URL: {RTSP_URL}", flush=True)
    print(f"✓ Device Service: http://0.0.0.0:{SERVER_PORT}/onvif/device_service", flush=True)
    print(f"✓ Media Service: http://0.0.0.0:{SERVER_PORT}/onvif/media_service", flush=True)
    print(f"✓ Snapshot URL: http://0.0.0.0:{SERVER_PORT}/snapshot.jpg", flush=True)
    print("\n" + "="*60 + "\n", flush=True)

    # Disable Flask/Werkzeug logging (keep only errors)
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.ERROR)

    logger.info("ONVIF server started for camera %s", CAMERA_ID[:8])

    # macvlan mode: bind to the specific camera IP so multiple ONVIF servers
    # can all use port 80 without conflicting (each on its own interface)
    bind_host = os.getenv('ONVIF_SERVER_IP', '0.0.0.0')

    try:
        # Use waitress for production-ready WSGI server (works better in subprocess)
        from waitress import serve
        print(f"✓ Server ready on http://{bind_host}:{SERVER_PORT}", flush=True)
        serve(app, host=bind_host, port=SERVER_PORT, threads=4)
    except ImportError:
        # Fallback to Flask development server
        print("⚠ waitress not available, using Flask dev server", flush=True)
        app.run(host=bind_host, port=SERVER_PORT, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"✗ Error starting server: {e}", flush=True)
        sys.exit(1)


if __name__ == '__main__':
    run_server()

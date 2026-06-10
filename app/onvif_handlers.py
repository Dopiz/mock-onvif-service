"""Pure SOAP response builders. Used by both the per-camera subprocess
(:mod:`onvif_server`) and the single in-process dispatcher
(:mod:`app.onvif_dispatcher`)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class OnvifContext:
    """Per-camera state needed to render ONVIF SOAP responses."""
    camera_id: str
    rtsp_url: str
    width: int
    height: int
    fps: float
    video_bitrate_kbps: int
    audio_bitrate_kbps: int
    sub_profile: bool = False
    manufacturer: str = "MockONVIF"
    shared_video_id: Optional[str] = None
    # SOAP responses derive the model from the port the client hit
    server_port: int = 12000

    @property
    def model(self) -> str:
        return f"{self.server_port}-{self.camera_id[:8]}"

    @property
    def serial_number(self) -> str:
        return self.camera_id

    @property
    def hardware_id(self) -> str:
        return f"dopiz-{self.camera_id[:8]}"


# Fixed 480p sub-profile parameters
SUB_PROFILE_WIDTH = 854
SUB_PROFILE_HEIGHT = 480
SUB_PROFILE_FPS = 24
SUB_PROFILE_BITRATE_KBPS = 1024


def _envelope(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"\n'
        '               xmlns:tds="http://www.onvif.org/ver10/device/wsdl"\n'
        '               xmlns:trt="http://www.onvif.org/ver10/media/wsdl"\n'
        '               xmlns:tt="http://www.onvif.org/ver10/schema">\n'
        f"  <soap:Body>{body}</soap:Body>\n"
        "</soap:Envelope>"
    )


def parse_action(xml_data: str) -> Optional[str]:
    try:
        root = ET.fromstring(xml_data)
        body = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Body")
        if body is not None and len(body) > 0:
            tag = body[0].tag
            return tag.split("}")[1] if "}" in tag else tag
    except Exception:
        return None
    return None


def extract_profile_token(xml_data: str) -> Optional[str]:
    try:
        root = ET.fromstring(xml_data)
        for elem in root.iter():
            if "ProfileToken" in elem.tag:
                return elem.text
    except Exception:
        return None
    return None


def extract_ws_security(xml_data: str) -> tuple[Optional[str], Optional[str]]:
    try:
        root = ET.fromstring(xml_data)
        ns = {
            "soap": "http://www.w3.org/2003/05/soap-envelope",
            "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
        }
        u = root.find(".//wsse:Username", ns)
        p = root.find(".//wsse:Password", ns)
        if u is not None and p is not None:
            return u.text, p.text
    except Exception:
        pass
    return None, None


# ── Device service ─────────────────────────────────────────────────────────
def get_device_information(ctx: OnvifContext) -> str:
    body = (
        "<tds:GetDeviceInformationResponse>"
        f"<tds:Manufacturer>{ctx.manufacturer}</tds:Manufacturer>"
        f"<tds:Model>{ctx.model}</tds:Model>"
        "<tds:FirmwareVersion>1.0.2</tds:FirmwareVersion>"
        f"<tds:SerialNumber>{ctx.serial_number}</tds:SerialNumber>"
        f"<tds:HardwareId>{ctx.hardware_id}</tds:HardwareId>"
        "</tds:GetDeviceInformationResponse>"
    )
    return _envelope(body)


def get_system_date_and_time() -> str:
    now = datetime.now(timezone.utc)
    body = (
        "<tds:GetSystemDateAndTimeResponse>"
        "<tds:SystemDateAndTime>"
        "<tt:DateTimeType>NTP</tt:DateTimeType>"
        "<tt:DaylightSavings>false</tt:DaylightSavings>"
        "<tt:TimeZone><tt:TZ>UTC</tt:TZ></tt:TimeZone>"
        "<tt:UTCDateTime>"
        f"<tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time>"
        f"<tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date>"
        "</tt:UTCDateTime>"
        "</tds:SystemDateAndTime>"
        "</tds:GetSystemDateAndTimeResponse>"
    )
    return _envelope(body)


def get_capabilities(server_ip: str, port: int) -> str:
    body = (
        "<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f"<tt:Device><tt:XAddr>http://{server_ip}:{port}/onvif/device_service</tt:XAddr></tt:Device>"
        f"<tt:Media><tt:XAddr>http://{server_ip}:{port}/onvif/media_service</tt:XAddr></tt:Media>"
        "</tds:Capabilities></tds:GetCapabilitiesResponse>"
    )
    return _envelope(body)


def get_services(server_ip: str, port: int) -> str:
    body = (
        "<tds:GetServicesResponse>"
        "<tds:Service>"
        "<tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{server_ip}:{port}/onvif/device_service</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        "</tds:Service>"
        "<tds:Service>"
        "<tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{server_ip}:{port}/onvif/media_service</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>0</tt:Minor></tds:Version>"
        "</tds:Service>"
        "</tds:GetServicesResponse>"
    )
    return _envelope(body)


# ── Media service ──────────────────────────────────────────────────────────
def _build_profile_xml(ctx: OnvifContext, token: str, name: str,
                      width: int, height: int, fps, vkbps: int,
                      vsource_token: str, vencoder_token: str) -> str:
    return (
        f'<trt:Profiles token="{token}" fixed="true">'
        f"<tt:Name>{name}</tt:Name>"
        f'<tt:VideoSourceConfiguration token="{vsource_token}">'
        "<tt:Name>VideoSourceConfig</tt:Name>"
        "<tt:SourceToken>VideoSource_1</tt:SourceToken>"
        f'<tt:Bounds x="0" y="0" width="{width}" height="{height}"/>'
        "</tt:VideoSourceConfiguration>"
        f'<tt:VideoEncoderConfiguration token="{vencoder_token}">'
        "<tt:Name>VideoEncoderConfig</tt:Name><tt:Encoding>H264</tt:Encoding>"
        f"<tt:Resolution><tt:Width>{width}</tt:Width><tt:Height>{height}</tt:Height></tt:Resolution>"
        "<tt:Quality>5</tt:Quality>"
        f"<tt:RateControl><tt:FrameRateLimit>{fps}</tt:FrameRateLimit><tt:BitrateLimit>{vkbps}</tt:BitrateLimit></tt:RateControl>"
        "</tt:VideoEncoderConfiguration>"
        '<tt:AudioSourceConfiguration token="AudioSource_1">'
        "<tt:Name>AudioSourceConfig</tt:Name><tt:SourceToken>AudioSource_1</tt:SourceToken>"
        "</tt:AudioSourceConfiguration>"
        '<tt:AudioEncoderConfiguration token="AudioEncoder_1">'
        "<tt:Name>AudioEncoderConfig</tt:Name><tt:Encoding>AAC</tt:Encoding>"
        f"<tt:Bitrate>{ctx.audio_bitrate_kbps}</tt:Bitrate><tt:SampleRate>16000</tt:SampleRate>"
        "<tt:SessionTimeout>PT60S</tt:SessionTimeout>"
        "</tt:AudioEncoderConfiguration>"
        '<tt:AudioOutputConfiguration token="AudioOutput_1">'
        "<tt:Name>AudioOutputConfig</tt:Name><tt:OutputToken>AudioOutput_1</tt:OutputToken>"
        "</tt:AudioOutputConfiguration>"
        "</trt:Profiles>"
    )


def get_profiles(ctx: OnvifContext) -> str:
    p1 = _build_profile_xml(ctx, "Profile_1", "MainProfile",
                            ctx.width, ctx.height, ctx.fps, ctx.video_bitrate_kbps,
                            "VideoSource_1", "VideoEncoder_1")
    p2 = ""
    if ctx.sub_profile:
        p2 = _build_profile_xml(ctx, "Profile_2", "SubProfile_480p",
                                SUB_PROFILE_WIDTH, SUB_PROFILE_HEIGHT,
                                SUB_PROFILE_FPS, SUB_PROFILE_BITRATE_KBPS,
                                "VideoSource_2", "VideoEncoder_2")
    body = f"<trt:GetProfilesResponse>{p1}{p2}</trt:GetProfilesResponse>"
    return _envelope(body)


def get_stream_uri(ctx: OnvifContext, profile_token: Optional[str]) -> str:
    rtsp_url = ctx.rtsp_url
    if ctx.sub_profile and profile_token == "Profile_2":
        rtsp_url = ctx.rtsp_url.replace(f"/{ctx.camera_id}", f"/{ctx.camera_id}_sub")
    body = (
        "<trt:GetStreamUriResponse><trt:MediaUri>"
        f"<tt:Uri>{rtsp_url}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT60S</tt:Timeout>"
        "</trt:MediaUri></trt:GetStreamUriResponse>"
    )
    return _envelope(body)


def get_snapshot_uri(server_ip: str, port: int) -> str:
    body = (
        "<trt:GetSnapshotUriResponse><trt:MediaUri>"
        f"<tt:Uri>http://{server_ip}:{port}/snapshot.jpg</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT10S</tt:Timeout>"
        "</trt:MediaUri></trt:GetSnapshotUriResponse>"
    )
    return _envelope(body)


def get_video_sources(ctx: OnvifContext) -> str:
    body = (
        '<trt:GetVideoSourcesResponse>'
        '<trt:VideoSources token="VideoSource_1">'
        f"<tt:Framerate>{ctx.fps}</tt:Framerate>"
        f"<tt:Resolution><tt:Width>{ctx.width}</tt:Width><tt:Height>{ctx.height}</tt:Height></tt:Resolution>"
        "</trt:VideoSources>"
        "</trt:GetVideoSourcesResponse>"
    )
    return _envelope(body)


def get_audio_sources() -> str:
    body = (
        '<trt:GetAudioSourcesResponse>'
        '<trt:AudioSources token="AudioSource_1"><tt:Channels>1</tt:Channels></trt:AudioSources>'
        '</trt:GetAudioSourcesResponse>'
    )
    return _envelope(body)


def get_audio_source_configurations() -> str:
    body = (
        '<trt:GetAudioSourceConfigurationsResponse>'
        '<trt:Configurations token="AudioSource_1">'
        "<tt:Name>AudioSourceConfig</tt:Name><tt:SourceToken>AudioSource_1</tt:SourceToken>"
        "</trt:Configurations></trt:GetAudioSourceConfigurationsResponse>"
    )
    return _envelope(body)


def get_audio_encoder_configurations(ctx: OnvifContext) -> str:
    body = (
        '<trt:GetAudioEncoderConfigurationsResponse>'
        '<trt:Configurations token="AudioEncoder_1">'
        "<tt:Name>AudioEncoderConfig</tt:Name><tt:Encoding>AAC</tt:Encoding>"
        f"<tt:Bitrate>{ctx.audio_bitrate_kbps}</tt:Bitrate>"
        "<tt:SampleRate>16000</tt:SampleRate><tt:SessionTimeout>PT60S</tt:SessionTimeout>"
        "</trt:Configurations>"
        "</trt:GetAudioEncoderConfigurationsResponse>"
    )
    return _envelope(body)


# ── Dispatch ───────────────────────────────────────────────────────────────
def dispatch_device(ctx: OnvifContext, xml_data: str, server_ip: str, port: int) -> str:
    action = parse_action(xml_data)
    if action == "GetDeviceInformation":
        return get_device_information(ctx)
    if action == "GetCapabilities":
        return get_capabilities(server_ip, port)
    if action == "GetSystemDateAndTime":
        return get_system_date_and_time()
    if action == "GetServices":
        return get_services(server_ip, port)
    return _envelope(f"<tds:{action}Response/>")


def dispatch_media(ctx: OnvifContext, xml_data: str, server_ip: str, port: int) -> str:
    action = parse_action(xml_data)
    if action == "GetProfiles":
        return get_profiles(ctx)
    if action == "GetStreamUri":
        return get_stream_uri(ctx, extract_profile_token(xml_data))
    if action == "GetSnapshotUri":
        return get_snapshot_uri(server_ip, port)
    if action == "GetVideoSources":
        return get_video_sources(ctx)
    if action == "GetAudioSources":
        return get_audio_sources()
    if action == "GetAudioSourceConfigurations":
        return get_audio_source_configurations()
    if action == "GetAudioEncoderConfigurations":
        return get_audio_encoder_configurations(ctx)
    return _envelope(f"<trt:{action}Response/>")

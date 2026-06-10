"""Parse SOAP responses from app/onvif_handlers.py with ElementTree."""
import xml.etree.ElementTree as ET

import pytest

from app.constants import (
    SUB_PROFILE_BITRATE_KBPS,
    SUB_PROFILE_FPS,
    SUB_PROFILE_HEIGHT,
    SUB_PROFILE_WIDTH,
)
from app.onvif_handlers import OnvifContext, dispatch_device, dispatch_media

NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}

CAMERA_ID = "8c058bc4-0000-4000-8000-000000000001"


def _soap_request(action: str, ns_uri: str, inner: str = "") -> str:
    return (
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
        f'<soap:Body><a:{action} xmlns:a="{ns_uri}">{inner}</a:{action}></soap:Body>'
        "</soap:Envelope>"
    )


def _ctx(**overrides) -> OnvifContext:
    kwargs = dict(
        camera_id=CAMERA_ID,
        rtsp_url=f"rtsp://192.168.1.10:8554/{CAMERA_ID}",
        width=1920, height=1080, fps=30.0,
        video_bitrate_kbps=4000, audio_bitrate_kbps=128,
        server_port=12345,
    )
    kwargs.update(overrides)
    return OnvifContext(**kwargs)


# ── Device service ──────────────────────────────────────────────────────────
class TestGetDeviceInformation:
    def test_manufacturer_and_serial(self):
        ctx = _ctx(manufacturer="Axis Communications")
        req = _soap_request("GetDeviceInformation", NS["tds"])
        root = ET.fromstring(dispatch_device(ctx, req, "1.2.3.4", 12345))
        assert root.find(".//tds:Manufacturer", NS).text == "Axis Communications"
        assert root.find(".//tds:SerialNumber", NS).text == CAMERA_ID
        assert root.find(".//tds:Model", NS).text == f"12345-{CAMERA_ID[:8]}"
        assert root.find(".//tds:HardwareId", NS).text == f"dopiz-{CAMERA_ID[:8]}"

    @pytest.mark.parametrize("hostile", [
        'Evil <Cam> & "Co"',
        "<script>alert(1)</script>",
        "a&b<c>d",
    ])
    def test_hostile_manufacturer_produces_well_formed_xml(self, hostile):
        ctx = _ctx(manufacturer=hostile)
        req = _soap_request("GetDeviceInformation", NS["tds"])
        xml = dispatch_device(ctx, req, "1.2.3.4", 12345)
        # must parse (i.e. special chars were escaped) ...
        root = ET.fromstring(xml)
        # ... and round-trip back to the original value
        assert root.find(".//tds:Manufacturer", NS).text == hostile
        # raw XML must contain the escaped form, not the raw injection
        assert "<script>" not in xml
        assert "&lt;" in xml or "&amp;" in xml

    def test_capabilities_xaddrs(self):
        req = _soap_request("GetCapabilities", NS["tds"])
        root = ET.fromstring(dispatch_device(_ctx(), req, "10.0.0.9", 12001))
        xaddrs = [e.text for e in root.iter(f"{{{NS['tt']}}}XAddr")]
        assert "http://10.0.0.9:12001/onvif/device_service" in xaddrs
        assert "http://10.0.0.9:12001/onvif/media_service" in xaddrs


# ── Media service ───────────────────────────────────────────────────────────
class TestGetStreamUri:
    def test_main_profile_rtsp_url(self):
        ctx = _ctx()
        inner = "<a:ProfileToken>Profile_1</a:ProfileToken>"
        req = _soap_request("GetStreamUri", NS["trt"], inner)
        root = ET.fromstring(dispatch_media(ctx, req, "1.2.3.4", 12345))
        assert root.find(".//tt:Uri", NS).text == ctx.rtsp_url

    def test_sub_profile_rtsp_url_has_sub_suffix(self):
        ctx = _ctx(sub_profile=True)
        inner = "<a:ProfileToken>Profile_2</a:ProfileToken>"
        req = _soap_request("GetStreamUri", NS["trt"], inner)
        root = ET.fromstring(dispatch_media(ctx, req, "1.2.3.4", 12345))
        assert root.find(".//tt:Uri", NS).text == (
            f"rtsp://192.168.1.10:8554/{CAMERA_ID}_sub"
        )


class TestGetProfiles:
    def _profiles(self, ctx):
        req = _soap_request("GetProfiles", NS["trt"])
        root = ET.fromstring(dispatch_media(ctx, req, "1.2.3.4", 12345))
        return root.findall(".//trt:Profiles", NS)

    def test_main_profile_resolution_matches_ctx(self):
        profiles = self._profiles(_ctx(width=1280, height=720, fps=25.0,
                                       video_bitrate_kbps=2500))
        assert len(profiles) == 1
        p = profiles[0]
        assert p.get("token") == "Profile_1"
        enc = p.find("tt:VideoEncoderConfiguration", NS)
        assert enc.find("tt:Resolution/tt:Width", NS).text == "1280"
        assert enc.find("tt:Resolution/tt:Height", NS).text == "720"
        assert enc.find("tt:RateControl/tt:FrameRateLimit", NS).text == "25.0"
        assert enc.find("tt:RateControl/tt:BitrateLimit", NS).text == "2500"

    def test_sub_profile_uses_constants(self):
        profiles = self._profiles(_ctx(sub_profile=True))
        assert len(profiles) == 2
        sub = profiles[1]
        assert sub.get("token") == "Profile_2"
        enc = sub.find("tt:VideoEncoderConfiguration", NS)
        assert enc.find("tt:Resolution/tt:Width", NS).text == str(SUB_PROFILE_WIDTH)
        assert enc.find("tt:Resolution/tt:Height", NS).text == str(SUB_PROFILE_HEIGHT)
        assert enc.find("tt:RateControl/tt:FrameRateLimit", NS).text == str(SUB_PROFILE_FPS)
        assert enc.find("tt:RateControl/tt:BitrateLimit", NS).text == str(SUB_PROFILE_BITRATE_KBPS)


def test_unknown_action_returns_empty_response_envelope():
    req = _soap_request("GetScopes", NS["tds"])
    root = ET.fromstring(dispatch_device(_ctx(), req, "1.2.3.4", 12345))
    body = root.find("soap:Body", NS)
    assert len(body) == 1
    assert body[0].tag.endswith("GetScopesResponse")

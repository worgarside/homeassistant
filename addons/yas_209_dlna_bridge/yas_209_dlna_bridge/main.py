"""Custom addon to unpack DLNA NOTIFY requests from the YAS-209 and POST them into HA"""
# pylint: disable=broad-except
from datetime import datetime
from enum import Enum

from flask import Flask, request
from json import dump
from logging import getLogger
from lxml.etree import fromstring, XMLSyntaxError
from pyexpat import ExpatError
from requests import post
from wg_utilities.functions import get_nsmap, force_mkdir
from wg_utilities.loggers import add_stream_handler
from xmltodict import parse as parse_xml

LOGGER = getLogger(__name__)
add_stream_handler(LOGGER)

app = Flask(__name__)
app.config["DEBUG"] = True

AVAILABILITY_POLL_PAYLOAD = """http-get:*:audio/wav:DLNA.ORG_PN=LPCM,http-get:*:audio/x-wav:DLNA.ORG_PN=LPCM,http-get:*:audio/mpeg:DLNA.ORG_PN=MP3,http-get:*:audio/mpeg:DLNA.ORG_PN=MP3X,http-get:*:audio/x-ms-wma:DLNA.ORG_PN=WMABASE,http-get:*:audio/x-ms-wma:DLNA.ORG_PN=WMAFULL,http-get:*:audio/x-ms-wma:DLNA.ORG_PN=WMAPRO,http-get:*:audio/mpeg:DLNA.ORG_PN=MP2_MPS,http-get:*:audio/mp3:*,http-get:*:audio/wma:*,http-get:*:audio/mpeg:*,http-get:*:audio/vnd.dlna.adts:DLNA.ORG_PN=AAC_ADTS,http-get:*:audio/vnd.dlna.adts:DLNA.ORG_PN=AAC_ADTS_320,http-get:*:audio/m4a:DLNA.ORG_PN=AAC_ISO,http-get:*:audio/aac:DLNA.ORG_PN=AAC_ISO,http-get:*:audio/ac3:DLNA.ORG_PN=AC3,http-get:*:audio/ogg:*,http-get:*:audio/ape:*,http-get:*:audio/x-ape:*,http-get:*:audio/flac:*"""  # pylint: disable=line-too-long


class MediaPlayerState(Enum):
    """Enumeration for states as the come in the DLNA payload"""

    PLAYING = "playing"
    PAUSED_PLAYBACK = "paused"
    STOPPED = "off"
    UNKNOWN = "unknown"


PAYLOAD = {}


def log_request_payload(event_xml):
    """Log the request payload locally for archiving etc.

    Args:
        event_xml (str): the XML document sent in the request
    """

    file_no_ext = datetime.now().strftime("./inbound_payloads/payload_%Y%m%d%H%M%S%f")

    force_mkdir("./inbound_payloads")

    with open(file_no_ext + ".xml", "w", encoding="utf-8") as fout:
        fout.write(event_xml)

    try:
        out_json = parse_xml(event_xml, attr_prefix="")
        if (
            ctm := out_json.get("Event", {})
            .get("InstanceID", {})
            .get("CurrentTrackMetaData", {})
            .get("val")
        ):
            out_json["Event"]["InstanceID"]["CurrentTrackMetaData"] = parse_xml(
                ctm, attr_prefix=""
            )
        with open(file_no_ext + ".json", "w", encoding="utf-8") as fout:
            dump(out_json, fout, indent=4)
    except ExpatError:
        if "CurrentTrackMetaData" not in event_xml:
            with open(file_no_ext + ".txt", "w", encoding="utf-8") as fout:
                fout.write(event_xml)


def update_payload_metadata(event_root, event_nsmap):
    """Update the metadata (song info) of the payload

    Args:
        event_root (Element): the etree root for the event XML
        event_nsmap (dict): the namespace map for the XML
    """
    try:
        metadata_attributes = event_root.xpath(
            "/default_0:Event/default_0:InstanceID/default_0:CurrentTrackMetaData",
            namespaces=event_nsmap,
        )[0].attrib
    except IndexError:
        return

    metadata_root = fromstring(
        metadata_attributes.get("val").replace("&", "&amp;").encode()
    )
    metadata_nsmap = get_nsmap(root=metadata_root)

    try:
        PAYLOAD["media_title"] = metadata_root.xpath(
            "//dc:title", namespaces=metadata_nsmap
        )[0].text
        PAYLOAD["media_artist"] = metadata_root.xpath(
            "//upnp:artist", namespaces=metadata_nsmap
        )[0].text
        PAYLOAD["media_album_name"] = metadata_root.xpath(
            "//upnp:album", namespaces=metadata_nsmap
        )[0].text
        if (
            album_art_uri := metadata_root.xpath(
                "//upnp:albumArtURI", namespaces=metadata_nsmap
            )[0].text
        ) == "un_known":
            album_art_uri = None
        PAYLOAD["album_art_uri"] = album_art_uri
    except Exception as exc:
        LOGGER.exception("%s - %s", type(exc).__name__, str(exc))


def update_payload_state(event_root, event_nsmap):
    """Update the entity state of the payload

    Args:
        event_root (Element): the etree root for the event XML
        event_nsmap (dict): the namespace map for the XML
    """

    try:
        raw_state_value = event_root.xpath(
            "/default_0:Event/default_0:InstanceID/default_0:TransportState",
            namespaces=event_nsmap,
        )[0].attrib["val"]
    except IndexError:
        return
    except Exception as exc:
        LOGGER.exception("%s - %s", type(exc).__name__, str(exc))
        raw_state_value = MediaPlayerState.UNKNOWN.name

    try:
        PAYLOAD["state"] = MediaPlayerState[raw_state_value].value
    except KeyError as exc:
        LOGGER.error("Unknown `MediaPlayerState`: %s", repr(exc))
        PAYLOAD["state"] = raw_state_value


def update_payload_volume_level(event_root, event_nsmap):
    """Update the volume of the payload

    Args:
        event_root (Element): the etree root for the event XML
        event_nsmap (dict): the namespace map for the XML
    """
    try:
        PAYLOAD["volume_level"] = event_root.xpath(
            "/default_0:Event/default_0:InstanceID/default_0:Volume",
            namespaces=event_nsmap,
        )[0].attrib["val"]
    except IndexError:
        pass


def pass_data_to_home_assistant(request_data):
    """Main worker here. Parses the inbound payload and sends it over to HA

    Args:
        request_data (str): the data from the NOTIFY request
    """
    event_xml = (
        fromstring(request_data.encode("latin-1"))
        .xpath(
            "/e:propertyset/e:property/LastChange",
            namespaces=dict(e="urn:schemas-upnp-org:event-1-0"),
        )[0]
        .text
    )

    if event_xml == AVAILABILITY_POLL_PAYLOAD:
        LOGGER.debug("Availability poll, ignoring")
        return

    log_request_payload(event_xml)

    try:
        event_root = fromstring(event_xml.encode())
        event_nsmap = get_nsmap(root=event_root)
    except XMLSyntaxError:
        return
    except Exception as exc:
        LOGGER.exception(
            "Exception parsing event root: %s - %s", type(exc).__name__, str(exc)
        )
        return

    update_payload_metadata(event_root, event_nsmap)
    update_payload_state(event_root, event_nsmap)
    update_payload_volume_level(event_root, event_nsmap)

    post(
        "http://192.168.1.120:8123/api/webhook/will_s_yas_209_event_data",
        json=PAYLOAD,
        headers={"Content-Type": "application/json"},
    )


@app.route(
    "/dlna_notify",
    methods=[
        "NOTIFY",
    ],
)
def notify():
    """API endpoint for getting the CRT state"""
    try:
        pass_data_to_home_assistant(request.data.decode("unicode_escape"))
    except Exception as exc:
        LOGGER.exception("%s - %s", type(exc).__name__, str(exc))
        raise

    return {}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
"""Functions which are only run on a certain trigger"""
from os.path import isfile
from socket import gethostname

from PIL import Image
from nanoleafapi import Nanoleaf
from requests import get
from wg_utilities.functions import force_mkdir

from helpers import get_secret, write_file

MODULE_NAME = "nanoleaf"

if gethostname() != "homeassistant":
    from helpers import local_setup

    log, task, sync_mock, decorator = local_setup()
    state = sync_mock
    sensor = sync_mock
    input_select = sync_mock
    input_boolean = sync_mock
    state_trigger = decorator
    service = decorator
    pyscript_executor = decorator


WILLS_SHAPES = task.executor(
    Nanoleaf,
    get_secret("ip_address", module="nanoleaf.wills_shapes"),
    get_secret("auth_token", module="nanoleaf.wills_shapes"),
)
THE_SNAIL = task.executor(
    Nanoleaf,
    get_secret("ip_address", module="nanoleaf.the_snail"),
    get_secret("auth_token", module="nanoleaf.the_snail"),
)


MATTS_SHAPES = task.executor(
    Nanoleaf,
    get_secret("ip_address", module="nanoleaf.matts_shapes"),
    get_secret("auth_token", module="nanoleaf.matts_shapes"),
)


@pyscript_executor
def get_n_colors_from_image(img_path, n):
    """Get the N most common colors from an image

    Args:
        img_path (str): the path to the image file
        n (int): the number of colors to retrieve from the image

    Returns:
        list: a list of the N most common colors in an image in the HSB format
    """

    pixels = Image.open(img_path).quantize(colors=n, method=0)

    return [
        {
            "hue": int((color_tuple[0] * 360) / 255),
            "saturation": int((color_tuple[1] * 100) / 255),
            "brightness": int((color_tuple[2] * 100) / 255),
        }
        for count, color_tuple in sorted(
            pixels.convert(mode="HSV").getcolors(),
            key=lambda elem: elem[0],
            reverse=True,
        )
    ][:n]


def get_local_artwork_file_path(artist, album, url):
    """Get the local file path for the artwork image, downloading it if necessary

    Args:
        artist (str): the name of the artist
        album (str): the name of the album
        url (str): the URL of the artwork, for if it needs to be downloaded

    Returns:
        str: the path to the artwork image file
    """

    cleansed_album_name = "".join([char.lower() for char in album if char.isalnum()])

    cleansed_artist_name = "".join([char.lower() for char in artist if char.isalnum()])

    if not isfile(
        target_path := "/".join(
            [
                "www",
                "album_artwork",
                cleansed_artist_name,
                f"{cleansed_album_name}.jpg",
            ]
        )
    ):
        res = task.executor(get, url)

        res.raise_for_status()

        log.info(
            "Album artwork endpoint response was `%s %s` - writing content to %s",
            res.status_code,
            res.reason,
            target_path,
        )

        write_file(
            force_mkdir(
                target_path,
                True,
            ),
            res.content,
            encoding=None,
        )

    return target_path


def update_nanoleaf_colors_worker(var_name, nanoleaf_device):
    """The actual worker function for updating Nanoleaf colors from an artwork image

    Args:
        var_name (str): the name of the entity which updated to cause this action
        nanoleaf_device (Nanoleaf): the actual Nanoleaf device to be updated
    """

    attributes = state.getattr(var_name)

    artwork_path = get_local_artwork_file_path(
        attributes["media_artist"],
        attributes["media_album_name"],
        attributes["entity_picture"],
    )

    effect_dict = {
        "command": "display",
        "animType": "random",
        "colorType": "HSB",
        "animData": None,
        "palette": get_n_colors_from_image(
            artwork_path,
            task.executor(nanoleaf_device.get_layout)["numPanels"] - 1,
        ),
        "transTime": {"minValue": 50, "maxValue": 100},
        "delayTime": {"minValue": 50, "maxValue": 100},
        "loop": True,
    }
    task.executor(nanoleaf_device.write_effect, effect_dict=effect_dict)


@state_trigger("media_player.all_speakers.media_title")
@state_trigger("media_player.downstairs_speakers.media_title")
@state_trigger("media_player.hifi_system.media_title")
@state_trigger("media_player.kitchen_nest_mini.media_title")
@state_trigger("media_player.matts_room_nest_mini.media_title")
@state_trigger("media_player.spotify_matt_scott.media_title")
@state_trigger("media_player.spotify_tom_jones.media_title")
@state_trigger("media_player.spotify_will_garside.media_title")
@state_trigger("media_player.tom_s_speakers.media_title")
@state_trigger("media_player.upstairs_speakers.media_title")
@state_trigger("media_player.will_s_yas_209.media_title")
def update_nanoleaf_colors(var_name):
    """Update Nanoleaf colors based on the currently playing song's artwork

    Args:
        var_name (str): the sensor which triggered this function
    """

    if (
        var_name == input_select.matts_shapes_artwork_colour_source
        and input_boolean.matts_shapes_artwork_colour_source_active == "on"
    ):
        update_nanoleaf_colors_worker(var_name, MATTS_SHAPES)

    if (
        var_name == input_select.the_snail_artwork_colour_source
        and input_boolean.the_snail_artwork_colour_source_active == "on"
    ):
        update_nanoleaf_colors_worker(var_name, THE_SNAIL)

    if (
        var_name == input_select.wills_shapes_artwork_colour_source
        and input_boolean.wills_shapes_artwork_colour_source_active == "on"
    ):
        update_nanoleaf_colors_worker(var_name, WILLS_SHAPES)
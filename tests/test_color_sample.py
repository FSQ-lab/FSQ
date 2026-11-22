# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from fsq_agent.drivers._color_sample import _sample_color
from fsq_agent.drivers.android._appium_connect import AppiumConnectAndroidDriver
from fsq_agent.drivers.ios._appium_connect import AppiumConnectIOSDriver
from fsq_agent.models import ConfigurationError, SampleColorParams


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"point": {"x": True, "y": 1}},
        {"point": {"x": -1, "y": 1}},
        {"region": {"left": 0, "right": 0, "top": 0, "bottom": 1}},
        {"point": {"x": 0, "y": 0}, "region": {"left": 0, "right": 1, "top": 0, "bottom": 1}},
    ],
)
def test_color_sample_rejects_ambiguous_or_invalid_geometry(payload):
    with pytest.raises(ValidationError):
        SampleColorParams.model_validate(payload)


def test_local_region_sampling_delivers_calculated_extrema():
    from appium_connect_agent.automation import sample_png_color

    image = Image.new("RGB", (4, 4), (0, 191, 243))
    image.putpixel((1, 1), (20, 200, 244))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    png = buffer.getvalue()

    class FrameDevice:
        def sample_color(self, x, y, *, region, coordinate_space, control):
            return sample_png_color(png, x, y, region=region, coordinate_space=coordinate_space)

    params = SampleColorParams.model_validate({"region": {"left": 0, "top": 0, "right": 2, "bottom": 2}, "coordinate_space": "image"})
    result = _sample_color(FrameDevice(), params, control=None)
    assert result["uniform"] is False
    assert result["min_rgb"] == (0, 191, 243)
    assert result["max_rgb"] == (20, 200, 244)


@pytest.mark.parametrize("platform", ["android", "ios"])
def test_mobile_color_sampling_preserves_safe_failure_details(platform, monkeypatch):
    class MappingError(RuntimeError):
        code = "pointer_mapping_unavailable"
        request_state = "response_received"

    secrets = "private SDK payload"

    def sample(*args, **kwargs):
        raise MappingError(secrets)

    def missing():
        raise ConfigurationError("Missing target")

    driver = AppiumConnectAndroidDriver(app_id="com.example.app") if platform == "android" else AppiumConnectIOSDriver()
    try:
        params = SampleColorParams.model_validate({"point": {"x": 0, "y": 1}})
        monkeypatch.setattr(driver, "_ensure_session", lambda: SimpleNamespace(device=SimpleNamespace(sample_color=sample)))
        result = driver.sample_color(params)
        assert result["failure_category"] == "observation_error"
        assert result["metadata"]["backend_code"] == "pointer_mapping_unavailable"
        assert result["metadata"]["request_state"] == "response_received"
        assert secrets not in str(result)
        monkeypatch.setattr(driver, "_ensure_session", missing)
        with pytest.raises(ConfigurationError, match="Missing target"):
            driver.sample_color(params)
    finally:
        driver.close()

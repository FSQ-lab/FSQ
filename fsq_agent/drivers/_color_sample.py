# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from dataclasses import asdict
from typing import Any

from fsq_agent.models import SampleColorParams


def _sample_color(device: Any, params: SampleColorParams, *, control: Any) -> dict[str, object]:
    from appium_connect_agent.automation import ImageRegion

    point, region = params.point, params.region
    return asdict(
        device.sample_color(
            point.x if point is not None else None,
            point.y if point is not None else None,
            region=ImageRegion(**region.model_dump()) if region is not None else None,
            coordinate_space=params.coordinate_space,
            control=control,
        )
    )

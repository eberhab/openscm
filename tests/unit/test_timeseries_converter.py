import re
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from openscm.core.time import ExtrapolationType, TimeseriesConverter
from openscm.errors import InsufficientDataError


def test_short_data(combo):
    timeseriesconverter = TimeseriesConverter(
        combo.source,
        combo.target,
        combo.timeseries_type,
        combo.interpolation_type,
        combo.extrapolation_type,
    )
    for a in [[], [0], [0, 1]]:
        with pytest.raises(InsufficientDataError):
            timeseriesconverter._convert(np.array(a), combo.source, combo.target)


def test_none_extrapolation_error(combo):
    target = pd.DatetimeIndex(
        [
            combo.source[0] - timedelta(seconds=1),
            combo.source[0],
            combo.source[-1] + timedelta(seconds=1),
        ]
    )
    timeseriesconverter = TimeseriesConverter(
        combo.source,
        target,
        combo.timeseries_type,
        combo.interpolation_type,
        ExtrapolationType.NONE,
    )
    error_msg = re.escape(
        "Target time points are outside the source time points, use an "
        "extrapolation type other than None"
    )
    with pytest.raises(InsufficientDataError, match=error_msg):
        timeseriesconverter._convert(combo.source_values, combo.source, target)

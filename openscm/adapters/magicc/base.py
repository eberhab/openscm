"""
Base adapter for MAGICC
"""
import warnings
from abc import abstractmethod, abstractproperty
from typing import Dict, Sequence, Union, cast

import numpy as np

from ...core.parameters import HierarchicalName, ParameterInfo, ParameterType
from ...errors import ParameterEmptyError
from ...scmdataframe import OpenScmDataFrame
from .. import Adapter

YEAR = 365 * 24 * 60 * 60  # example time step length as used below

# Zeb's scribbles as he does this (may be helpful for later):
#   - MAGICC doesn't need to have a constant timestep, hence can't directly copy the
#     examples of DICE or PH99
#   - _initialize_model needs to do initalize (create copy of MAGICC)
#   - _shutdown needs to do the cleanup (delete copy of MAGICC)
#   - _set_model_from_parameters needs to re-write the input files
#   - reset needs to delete everything in `out` and cleanup the output parameters
#   - _run just calls the .run method
#   - _step is not implemented
#   - _get_time_points can just use the openscm parameters (only need to pass back
#       down at set_model_from_parameters calls)
#   - _update_model just calls MAGICC's update_config method
#   - name is MAGICC6 (as it's what is used in OpenSCM parameters)
#   - tests
#       - start by reading through tests of DICE and PH99 for inspiration of what to
#         test
#   - follow lead of PH99 for implementation as it has a separate model module


class _MAGICCBase(Adapter):
    """
    Base adapter for different MAGICC versions.

    The Model for the Assessment of Greenhouse Gas Induced Climate Change (MAGICC)
    projects atmospheric greenhouse gas concentrations, radiative forcing of
    greenhouse gases and aerosols, hemispheric and land/ocean surface temperatures and
    sea-level rise from projected emissions of greenhouse gases and aerosols (its
    historical emissions/concentrations can also be specified but this functionality
    is not yet provided).
    """

    _openscm_standard_parameter_mappings: Dict[Sequence[str], str] = {
        "Equilibrium Climate Sensitivity": "core_climatesensitivity",
        "Radiative Forcing 2xCO2": "core_delq2xco2",
        "Start Time": "startyear",
        "Stop Time": "endyear",
    }

    _openscm_output_mappings = {"Surface Temperature Increase": "Surface Temperature"}

    _internal_timeseries_conventions = {
        "Atmospheric Concentrations": "point",
        "Emissions": "point",
        "Radiative Forcing": "point",
        "Temperatures": "point",
    }

    _units = {"core_climatesensitivity": "delta_degC", "core_delq2xco2": "W/m^2"}

    @abstractproperty
    def name(self):
        """
        Name of the model as used in OpenSCM parameters
        """

    @abstractmethod
    def _initialize_model(self) -> None:
        pass

    def _initialize_generic_view(self, full_name, value):
        self._add_parameter_view(full_name, value)
        model_name = full_name[1]
        imap = self._inverse_openscm_standard_parameter_mappings
        if model_name in imap:
            openscm_name = imap[model_name]
            self._add_parameter_view(openscm_name)

    def _initialize_scalar_view(self, full_name, value, unit):
        model_name = full_name[1]
        imap = self._inverse_openscm_standard_parameter_mappings

        self._add_parameter_view(full_name, value, unit=unit)
        if model_name in imap:
            openscm_name = imap[model_name]
            if openscm_name in ("Start Time", "Stop Time"):
                self._add_parameter_view(openscm_name)
            else:
                self._add_parameter_view(openscm_name, unit=unit)

    def _initialize_timeseries_view(self, full_name, unit):
        top_key = full_name[0]
        self._add_parameter_view(
            full_name,
            unit=unit,
            timeseries_type=self._internal_timeseries_conventions[top_key],
        )

    def _get_magcfg_default_value(self, magicc_name):
        if magicc_name in ("startyear", "endyear", "stepsperyear"):
            return self.model.default_config["nml_years"][magicc_name]

        return self.model.default_config["nml_allcfgs"][magicc_name]

    def _shutdown(self) -> None:
        self.model.remove_temp_copy()

    def _get_time_points(
        self, timeseries_type: Union[ParameterType, str]
    ) -> np.ndarray:
        if self._timeseries_time_points_require_update():

            def get_time_points(tt):
                end_year = self._end_time.astype(object).year
                if tt == "average":
                    end_year += 1

                return np.array(
                    [
                        np.datetime64("{}-01-01".format(y))
                        for y in range(
                            self._start_time.astype(object).year, end_year + 1
                        )
                    ]
                ).astype("datetime64[s]")

            self._time_points = get_time_points("point")
            self._time_points_for_averages = get_time_points("average")

        return (
            self._time_points
            if timeseries_type in ("point", ParameterType.POINT_TIMESERIES)
            else self._time_points_for_averages
        )

    def _timeseries_time_points_require_update(
        self, names_to_check: list = ["Start Time", "Stop Time", "Step Length"]
    ) -> bool:
        return super()._timeseries_time_points_require_update(
            names_to_check=["Start Time", "Stop Time"]
        )

    def _set_model_from_parameters(self):
        super()._set_model_from_parameters()

        if self._write_out_emissions:
            self._write_emissions_to_file()
            self._write_out_emissions = False

    def _write_emissions_to_file(self):
        raise NotImplementedError

    def _update_model(self, name: HierarchicalName, para: ParameterInfo) -> None:
        timeseries_types = (
            ParameterType.AVERAGE_TIMESERIES,
            ParameterType.POINT_TIMESERIES,
        )
        value = self._get_parameter_value(para)
        if name in self._openscm_standard_parameter_mappings:
            self._set_model_para_from_openscm_para(name, value)
        else:
            if para.parameter_type in timeseries_types:
                self._write_out_emissions = True
                return

            if name[0] != self.name:
                # emergency valve for now, must be smarter way to handle this
                raise ValueError(
                    "How did non-{} parameter end up here?".format(self.name)
                )

            self._run_kwargs[name[1]] = value

    def _set_model_para_from_openscm_para(self, openscm_name, value):
        magicc_name = self._openscm_standard_parameter_mappings[openscm_name]

        if magicc_name in ("startyear", "endyear"):
            self._run_kwargs[magicc_name] = value.astype(object).year
        else:
            self._run_kwargs[magicc_name] = value

    def _reset(self) -> None:
        # hack hack hack
        for (
            _,
            v,
        ) in self._output._root._parameters.items():  # pylint:disable=protected-access
            if v.unit is None:
                continue

            para_type = cast(ParameterType, v.parameter_type)
            tp = self._get_time_points(para_type)
            view = self._output.timeseries(
                v.name, v.unit, time_points=tp, timeseries_type=para_type
            )
            view.values = np.zeros(tp.shape) * np.nan

    def _run(self) -> None:
        if "startyear" in self._run_kwargs:
            self._run_kwargs.pop("startyear")
            warnings.warn(
                "MAGICC is hard-coded to start in 1765 as there is a conflict between the concept of a start year and having continuous timeseries"
            )

        res = self.model.run(startyear=1765, **self._run_kwargs)
        # hack hack hack
        res_tmp = (
            res.filter(region="World")
            .filter(unit=["*CO2eq*"], keep=False)
            .timeseries()
            .reset_index()
        )
        res_tmp["climate_model"] = "unspecified"

        imap = {v: k for k, v in self._openscm_output_mappings.items()}
        res_tmp["variable"] = res_tmp["variable"].apply(
            lambda x: imap[x] if x in imap else x
        )
        res_tmp["parameter_type"] = res_tmp["variable"].apply(
            lambda x: self._internal_timeseries_conventions[x.split("|")[0]]
            if x.split("|")[0] in self._internal_timeseries_conventions
            else "point"
        )

        # need to keep more than just world at some point in future, currently
        # hierarchy doesn't work...
        res_tmp = OpenScmDataFrame(res_tmp)
        # how to solve fact that not all radiative forcing is reported all the time (
        # parameterset doesn't work if you try to write e.g. `Radiative Forcing` and
        # `Radiative Forcing|Greenhouse Gases`, `Radiative Forcing` will always be
        # calculated from its children so need to report all the sub-components or
        # none)
        res_tmp.filter(variable="Radiative Forcing|*", keep=False).to_parameterset(
            parameterset=self._output
        )

        for _, nml_values in res.metadata["parameters"].items():
            for k, v in nml_values.items():
                if k in self._units:
                    self._output.scalar((self.name, k), self._units[k]).value = v
                else:
                    warnings.warn("Not returning parameters without units")
                    # self._output.generic(
                    #     (self.name, k),
                    # ).value = v

    def _step(self) -> None:
        raise NotImplementedError

    @property
    def _start_time(self):
        st = super()._start_time
        if isinstance(st, (float, int)):
            if int(st) != st:
                raise ValueError(
                    "('{}', 'startyear') should be an integer".format(self.name)
                )
            return np.datetime64("{}-01-01".format(int(st)))
        return st

    @property
    def _end_time(self):
        try:
            return self._parameter_views["Stop Time"].value
        except ParameterEmptyError:
            et = self._parameter_views[
                (self.name, self._openscm_standard_parameter_mappings["Stop Time"])
            ].value
            if isinstance(et, (int, float)):
                if int(et) != et:
                    raise ValueError(
                        "('{}', 'endyear') should be an integer".format(self.name)
                    )
            return np.datetime64("{}-01-01".format(int(et)))

    @property
    def _timestep_count(self):
        # MAGICC6 is always run with yearly drivers, the `stepsperyear` parameter is
        # internal only so can be ignored
        return (
            self._end_time.astype(object).year
            - self._start_time.astype(object).year
            + 1
        )

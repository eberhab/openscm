"""
Unit handling.

..
   TODO: move this documentation to a separate rst document

Unit handling makes use of the `Pint <https://github.com/hgrecco/pint>`_ library. This
allows us to easily define units as well as contexts. Contexts allow us to perform
conversions which would not normally be allowed e.g. in the 'AR4GWP100'
context we can
convert from CO2 to CH4 using the AR4GWP100 equivalence metric.

In general, you should not use Pint with OpenSCM explicitly. As illustration of how
units are used internally, we provide the following example:

.. code:: python

    >>> from openscm.units import _unit_registry
    >>> _unit_registry("CO2")
    <Quantity(1, 'CO2')>

    >>> emissions_aus = 0.34 * _unit_registry("Gt C / yr")
    >>> emissions_aus
    <Quantity(0.34, 'C * gigametric_ton / a')>

    >>> emissions_aus.to("Mt C / week")
    <Quantity(6.516224050620789, 'C * megametric_ton / week')>

**A note on emissions units**

Emissions are a flux composed of three parts: mass, the species being emitted and the
time period e.g. "t CO2 / yr". As mass and time are part of SI units, all we need to
define here are emissions units i.e. the stuff. Here we include as many of the canonical
emissions units, and their conversions, as possible.

For emissions units, there are a few cases to be considered:

- fairly obvious ones e.g. carbon dioxide emissions can be provided in 'C' or 'CO2' and
  converting between the two is possible
- less obvious ones e.g. nitrous oxide emissions can be provided in 'N', 'N2O' or
  'N2ON', we provide conversions
- case-sensitivity. In order to provide a simplified interface, using all uppercase
  versions of any unit is also valid e.g. ``unit_registry("HFC4310mee")`` is the same as
  ``unit_registry("HFC4310MEE")``
- hyphens and underscores in units. In order to be Pint compatible and to simplify
  things, we strip all hyphens and underscores from units.

As a convenience, we allow users to combine the mass and the type of emissions to make a
'joint unit' e.g. "tCO2" but it should be recognised that this joint unit is a derived
unit and not a base unit.

By defining these three separate components, it is much easier to track what conversions
are valid and which are not. For example, as the emissions units are all defined as
emissions units, and not as atomic masses, we are able to prevent invalid conversions.
If emissions units were simply atomic masses, it would be possible to convert between
e.g. C and N2O which would be a problem. Conventions such as allowing carbon dioxide
emissions to be reported in C or CO2, despite the fact that they are fundamentally
different chemical species, is a convention which is particular to emissions (as far as
we can tell).

Finally, contexts are particularly useful for emissions as they facilitate much easier
metric conversions. With a context, a conversion which wouldn't normally be allowed
(e.g. tCO2 --> tN2O) is allowed and will use whatever metric conversion is appropriate
for that context (e.g. AR4GWP100).

Finally, we discuss namespace collisions.

*CH4*

Methane emissions are defined as 'CH4'. In order to prevent inadvertent conversions of
'CH4' to e.g. 'CO2' via 'C', the conversion 'CH4' <--> 'C' is by default forbidden.
However, it can be performed within the context 'CH4_conversions' as shown below:

.. code:: python

    >>> from openscm.units import UnitConverter
    >>> uc = UnitConverter("CH4", "C")
    pint.errors.DimensionalityError: Cannot convert from 'CH4' ([methane]) to 'C' ([carbon])

    # with a context, the conversion becomes legal again
    >>> uc = UnitConverter("CH4", "C", context="CH4_conversions")
    >>> uc.convert_from(1)
    0.75

    # as an unavoidable side effect, this also becomes possible
    >>> uc = UnitConverter("CH4", "CO2", context="CH4_conversions")
    >>> uc.convert_from(1)
    2.75

*NOx*

Like for methane, NOx emissions also suffer from a namespace collision. In order to
prevent inadvertent conversions from 'NOx' to e.g. 'N2O', the conversion 'NOx' <-->
'N' is by default forbidden. It can be performed within the 'NOx_conversions' context:

.. code:: python

    >>> from openscm.units import unit_registry
    >>> uc = UnitConverter("NOx", "N")
    pint.errors.DimensionalityError: Cannot convert from 'NOx' ([NOx]) to 'N' ([nitrogen])

    # with a context, the conversion becomes legal again
    >>> uc = UnitConverter("NOx", "N", context="NOx_conversions")
    >>> uc.convert_from(1)
    0.30434782608695654

    # as an unavoidable side effect, this also becomes possible
    >>> uc = UnitConverter("NOx", "N2O", context="NOx_conversions")
    >>> uc.convert_from(1)
    0.9565217391304348
"""
import warnings
from typing import Dict, Optional, Sequence, Union

import numpy as np
import pint
from pint.errors import (  # noqa: F401 # pylint: disable=unused-import
    DimensionalityError,
    UndefinedUnitError,
)

# Standard gases. If the value is:
# - str: this entry defines a base gas unit
# - list: this entry defines a derived unit
#    - the first entry defines how to convert from base units
#    - other entries define other names i.e. aliases
_standard_gases = {
    # CO2, CH4, N2O
    "C": "carbon",
    "CO2": ["12/44 * C", "carbon_dioxide"],
    "CH4": "methane",
    "N": "nitrogen",
    "N2O": ["14/44 * N", "nitrous_oxide"],
    "N2ON": ["14/28 * N", "nitrous_oxide_farming_style"],
    # aerosol precursors
    "NOx": "NOx",
    "nox": ["NOx"],
    "NH3": ["14/17 * N", "ammonia"],
    "S": ["sulfur"],
    "SO2": ["32/64 * S", "sulfur_dioxide"],
    "SOx": ["SO2"],
    "BC": "black_carbon",
    "OC": "OC",
    "CO": "carbon_monoxide",
    "VOC": "VOC",
    "NMVOC": ["VOC", "non_methane_volatile_organic_compounds"],
    # CFCs
    "CFC11": "CFC11",
    "CFC12": "CFC12",
    "CFC13": "CFC13",
    "CFC113": "CFC113",
    "CFC114": "CFC114",
    "CFC115": "CFC115",
    # HCFCs
    "HCFC21": "HCFC21",
    "HCFC22": "HCFC22",
    "HCFC123": "HCFC123",
    "HCFC124": "HCFC124",
    "HCFC141b": "HCFC141b",
    "HCFC142b": "HCFC142b",
    "HCFC225ca": "HCFC225ca",
    "HCFC225cb": "HCFC225cb",
    # HFCs
    "HFC23": "HFC23",
    "HFC32": "HFC32",
    "HFC41": "HFC41",
    "HFC125": "HFC125",
    "HFC134": "HFC134",
    "HFC134a": "HFC134a",
    "HFC143": "HFC143",
    "HFC143a": "HFC143a",
    "HFC152": "HFC152",
    "HFC152a": "HFC152a",
    "HFC161": "HFC161",
    "HFC227ea": "HFC227ea",
    "HFC236cb": "HFC236cb",
    "HFC236ea": "HFC236ea",
    "HFC236fa": "HFC236fa",
    "HFC245ca": "HFC245ca",
    "HFC245fa": "HFC245fa",
    "HFC365mfc": "HFC365mfc",
    "HFC4310mee": "HFC4310mee",
    "HFC4310": ["HFC4310mee"],
    # Halogenated gases
    "Halon1201": "Halon1201",
    "Halon1202": "Halon1202",
    "Halon1211": "Halon1211",
    "Halon1301": "Halon1301",
    "Halon2402": "Halon2402",
    # PFCs
    "CF4": "CF4",
    "C2F6": "C2F6",
    "cC3F6": "cC3F6",
    "C3F8": "C3F8",
    "cC4F8": "cC4F8",
    "C4F10": "C4F10",
    "C5F12": "C5F12",
    "C6F14": "C6F14",
    "C7F16": "C7F16",
    "C8F18": "C8F18",
    "C10F18": "C10F18",
    # Fluorinated ethers
    "HFE125": "HFE125",
    "HFE134": "HFE134",
    "HFE143a": "HFE143a",
    "HCFE235da2": "HCFE235da2",
    "HFE245cb2": "HFE245cb2",
    "HFE245fa2": "HFE245fa2",
    "HFE347mcc3": "HFE347mcc3",
    "HFE347pcf2": "HFE347pcf2",
    "HFE356pcc3": "HFE356pcc3",
    "HFE449sl": "HFE449sl",
    "HFE569sf2": "HFE569sf2",
    "HFE4310pccc124": "HFE4310pccc124",
    "HFE236ca12": "HFE236ca12",
    "HFE338pcc13": "HFE338pcc13",
    "HFE227ea": "HFE227ea",
    "HFE236ea2": "HFE236ea2",
    "HFE236fa": "HFE236fa",
    "HFE245fa1": "HFE245fa1",
    "HFE263fb2": "HFE263fb2",
    "HFE329mcc2": "HFE329mcc2",
    "HFE338mcf2": "HFE338mcf2",
    "HFE347mcf2": "HFE347mcf2",
    "HFE356mec3": "HFE356mec3",
    "HFE356pcf2": "HFE356pcf2",
    "HFE356pcf3": "HFE356pcf3",
    "HFE365mcf3": "HFE365mcf3",
    "HFE374pc2": "HFE374pc2",
    # Perfluoropolyethers
    "PFPMIE": "PFPMIE",
    # Misc
    "CCl4": "CCl4",
    "CHCl3": "CHCl3",
    "CH2Cl2": "CH2Cl2",
    "CH3CCl3": "CH3CCl3",
    "CH3Cl": "CH3Cl",
    "CH3Br": "CH3Br",
    "SF5CF3": "SF5CF3",
    "SF6": "SF6",
    "NF3": "NF3",
}


class ScmUnitRegistry(pint.UnitRegistry):  # type: ignore
    """
    Unit registry class for OpenSCM. Provides some convenience methods to add standard
    unit and contexts.
    """

    _contexts_loaded: bool = False

    def add_standards(self):
        """
        Add standard units.

        Has to be done separately because of pint's weird initializing.
        """
        self._add_gases(_standard_gases)

        self.define("a = 1 * year = annum = yr")
        self.define("h = hour")
        self.define("d = day")
        self.define("degreeC = degC")
        self.define("degreeF = degF")
        self.define("kt = 1000 * t")  # since kt is used for "knot" in the defaults

        self.define("ppt = [concentrations]")
        self.define("ppb = 1000 * ppt")
        self.define("ppm = 1000 * ppb")

    def enable_contexts(self, *names_or_contexts, **kwargs):
        """
        Overload pint's :func:`enable_contexts` to load contexts once (the first time
        they are used) to avoid (unnecessary) file operations on import.
        """
        if not self._contexts_loaded:
            self._load_contexts()
        self._contexts_loaded = True
        super().enable_contexts(*names_or_contexts, **kwargs)

    def _add_mass_emissions_joint_version(self, symbol: str) -> None:
        """
        Add a unit which is the combination of mass and emissions.

        This allows users to units like e.g. ``"tC"`` rather than requiring a space
        between the mass and the emissions i.e. ``"t C"``

        Parameters
        ----------
        symbol
            The unit to add a joint version for
        """
        self.define("g{symbol} = g * {symbol}".format(symbol=symbol))
        self.define("t{symbol} = t * {symbol}".format(symbol=symbol))

    def _add_gases(self, gases: Dict[str, Union[str, Sequence[str]]]) -> None:
        for symbol, value in gases.items():
            if isinstance(value, str):
                # symbol is base unit
                self.define("{} = [{}]".format(symbol, value))
                if value != symbol:
                    self.define("{} = {}".format(value, symbol))
            else:
                # symbol has conversion and aliases
                self.define("{} = {}".format(symbol, value[0]))
                for alias in value[1:]:
                    self.define("{} = {}".format(alias, symbol))

            self._add_mass_emissions_joint_version(symbol)

            # Add alias for upper case symbol:
            if symbol.upper() != symbol:
                self.define("{} = {}".format(symbol.upper(), symbol))
                self._add_mass_emissions_joint_version(symbol.upper())

    def _load_contexts(self) -> None:
        """
        Load contexts.
        """
        _ch4_context = pint.Context("CH4_conversions")
        _ch4_context.add_transformation(
            "[carbon]",
            "[methane]",
            lambda registry, x: 16 / 12 * registry.CH4 * x / registry.C,
        )
        _ch4_context.add_transformation(
            "[methane]",
            "[carbon]",
            lambda registry, x: x * registry.C / registry.CH4 / (16 / 12),
        )
        self.add_context(_ch4_context)

        _n2o_context = pint.Context("NOx_conversions")
        _n2o_context.add_transformation(
            "[nitrogen]",
            "[NOx]",
            lambda registry, x: (14 + 2 * 16)
            / 14
            * registry.NOx
            * x
            / registry.nitrogen,
        )
        _n2o_context.add_transformation(
            "[NOx]",
            "[nitrogen]",
            lambda registry, x: x
            * registry.nitrogen
            / registry.NOx
            / ((14 + 2 * 16) / 14),
        )
        self.add_context(_n2o_context)

        self._load_metric_conversions()

    def _load_metric_conversions(self) -> None:
        """
        Load metric conversion contexts from file.

        This is done only when contexts are needed to avoid reading files on import.
        """
        import pandas as pd
        from os import path

        metric_conversions = pd.read_csv(
            path.join(
                path.dirname(path.abspath(__file__)),
                "..",
                "data",
                "metric_conversions.csv",
            ),
            skiprows=1,  # skip source row
            header=0,
            index_col=0,
        ).iloc[
            1:, :
        ]  # drop out 'OpenSCM base unit' row

        def _get_transform_func(ureg_unit, conversion_factor, forward=True):
            if forward:

                def result_forward(ur, strt):
                    return strt * ur.carbon / ureg_unit * conversion_factor

                return result_forward

            def result_backward(ur, strt):
                return strt * ureg_unit / ur.carbon / conversion_factor

            return result_backward

        for col in metric_conversions:
            tc = pint.Context(col)
            for label, val in metric_conversions[col].iteritems():
                conv_val = (
                    val
                    * (self("CO2").to_base_units()).magnitude
                    / (self(label).to_base_units()).magnitude
                )
                base_unit = [
                    s
                    for s, _ in self._get_dimensionality(
                        self(label)  # pylint: disable=protected-access
                        .to_base_units()
                        ._units
                    ).items()
                ][0]

                unit_reg_unit = getattr(
                    self, base_unit.replace("[", "").replace("]", "")
                )
                tc.add_transformation(
                    base_unit, "[carbon]", _get_transform_func(unit_reg_unit, conv_val)
                )
                tc.add_transformation(
                    "[carbon]",
                    base_unit,
                    _get_transform_func(unit_reg_unit, conv_val, forward=False),
                )
                tc.add_transformation(
                    "[mass] * {} / [time]".format(base_unit),
                    "[mass] * [carbon] / [time]",
                    _get_transform_func(unit_reg_unit, conv_val),
                )
                tc.add_transformation(
                    "[mass] * [carbon] / [time]",
                    "[mass] * {} / [time]".format(base_unit),
                    _get_transform_func(unit_reg_unit, conv_val, forward=False),
                )
                tc.add_transformation(
                    "[mass] * {}".format(base_unit),
                    "[mass] * [carbon]",
                    _get_transform_func(unit_reg_unit, conv_val),
                )
                tc.add_transformation(
                    "[mass] * [carbon]",
                    "[mass] * {}".format(base_unit),
                    _get_transform_func(unit_reg_unit, conv_val, forward=False),
                )
                tc.add_transformation(
                    "{} / [time]".format(base_unit),
                    "[carbon] / [time]",
                    _get_transform_func(unit_reg_unit, conv_val),
                )
                tc.add_transformation(
                    "[carbon] / [time]",
                    "{} / [time]".format(base_unit),
                    _get_transform_func(unit_reg_unit, conv_val, forward=False),
                )

            self.add_context(tc)


_unit_registry = ScmUnitRegistry()
"""
OpenSCM standard unit registry

The unit registry contains all of the recognised units.
"""
_unit_registry.add_standards()


class UnitConverter:
    """
    Converts numbers between two units.
    """

    _source: str
    """Source unit"""

    _target: str
    """Target unit"""

    _offset: float
    """Offset for units (e.g. for temperature units)"""

    _scaling: float
    """Scaling factor between units"""

    def __init__(self, source: str, target: str, context: Optional[str] = None):
        """
        Initialize.

        Parameters
        ----------
        source
            Unit to convert **from**
        target
            Unit to convert **to**
        context
            Context to use for the conversion i.e. which metric to apply when performing
            CO2-equivalent calculations. If ``None``, no metric will be applied and
            CO2-equivalent calculations will raise :class:`DimensionalityError`.

        Raises
        ------
        pint.errors.DimensionalityError
            Units cannot be converted into each other.
        pint.errors.UndefinedUnitError
            Unit undefined.
        """
        self._source = source
        self._target = target

        source_unit = _unit_registry.Unit(source)
        target_unit = _unit_registry.Unit(target)

        s1 = _unit_registry.Quantity(1, source_unit)
        s2 = _unit_registry.Quantity(-1, source_unit)

        if context is None:
            t1 = s1.to(target_unit)
            t2 = s2.to(target_unit)
        else:
            with _unit_registry.context(context):
                t1 = s1.to(target_unit)
                t2 = s2.to(target_unit)

        if np.isnan(t1) or np.isnan(t2):
            warn_msg = (
                "No conversion from {} to {} available, nan will be returned "
                "upon conversion".format(source, target)
            )
            warnings.warn(warn_msg)

        self._scaling = float(t2.m - t1.m) / float(s2.m - s1.m)
        self._offset = t1.m - self._scaling * s1.m

    def convert_from(self, v: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """
        Convert value **from** source unit to target unit.

        Parameters
        ----------
        value
            Value in source unit

        Returns
        -------
        Union[float, np.ndarray]
            Value in target unit
        """
        return self._offset + v * self._scaling

    def convert_to(self, v: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """
        Convert value from target unit **to** source unit.

        Parameters
        ----------
        value
            Value in target unit

        Returns
        -------
        Union[float, np.ndarray]
            Value in source unit
        """
        return (v - self._offset) / self._scaling

    @property
    def contexts(self) -> Sequence[str]:
        """
        Available contexts for unit conversions
        """
        return list(_unit_registry._contexts.keys())  # pylint: disable=protected-access

    @property
    def unit_registry(self) -> ScmUnitRegistry:
        """
        Underlying unit registry
        """
        return _unit_registry

    @property
    def source(self) -> str:
        """
        Source unit
        """
        return self._source

    @property
    def target(self) -> str:
        """
        Target unit
        """
        return self._target

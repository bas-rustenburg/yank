#!/usr/bin/env python

# =============================================================================
# MODULE DOCSTRING
# =============================================================================

"""
Test State classes in states.py.

"""

# =============================================================================
# GLOBAL IMPORTS
# =============================================================================

import nose
from simtk import unit
from openmmtools import testsystems

from yank.states import *


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_barostat_temperature(barostat):
    """Backward-compatibly get barostat's temperature"""
    try:  # TODO drop this when we stop openmm7.0 support
        return barostat.getDefaultTemperature()
    except AttributeError:  # versions previous to OpenMM 7.1
        return barostat.setTemperature()


# =============================================================================
# UTILITY CLASSES
# =============================================================================

class InconsistentThermodynamicState(ThermodynamicState):
    """ThermodynamicState that does not run consistency checks on init.

    It is useful to test private methods used to check for consistency.

    """
    def __init__(self, system=None, temperature=None, pressure=None):
        self._system = copy.deepcopy(system)
        self._temperature = temperature


# =============================================================================
# TEST THERMODYNAMIC STATE
# =============================================================================

class TestThermodynamicState(object):

    @classmethod
    def setup_class(cls):
        """Create the test systems used in the test suite."""
        cls.temperature = 300*unit.kelvin
        cls.pressure = 1.0*unit.atmosphere
        cls.toluene_vacuum = testsystems.TolueneVacuum().system
        cls.toluene_implicit = testsystems.TolueneImplicit().system
        cls.alanine_explicit = testsystems.AlanineDipeptideExplicit().system

        # A system correctly barostated
        cls.barostated_alanine = copy.deepcopy(cls.alanine_explicit)
        barostat = openmm.MonteCarloBarostat(cls.pressure, cls.temperature)
        cls.barostated_alanine.addForce(barostat)

        # A non-periodic system barostated
        cls.barostated_toluene = copy.deepcopy(cls.toluene_vacuum)
        barostat = openmm.MonteCarloBarostat(cls.pressure, cls.temperature)
        cls.barostated_toluene.addForce(barostat)

        # A system with two identical MonteCarloBarostats
        cls.multiple_barostat_alanine = copy.deepcopy(cls.barostated_alanine)
        barostat = openmm.MonteCarloBarostat(cls.pressure, cls.temperature)
        cls.multiple_barostat_alanine.addForce(barostat)

        # A system with an unsupported MonteCarloAnisotropicBarostat
        cls.unsupported_barostat_alanine = copy.deepcopy(cls.alanine_explicit)
        pressure_in_bars = cls.pressure / unit.bar
        anisotropic_pressure = openmm.Vec3(pressure_in_bars, pressure_in_bars,
                                           pressure_in_bars)
        barostat = openmm.MonteCarloAnisotropicBarostat(anisotropic_pressure,
                                                        cls.temperature)
        cls.unsupported_barostat_alanine.addForce(barostat)


    def test_method_find_barostat(self):
        """ThermodynamicState._find_barostat() method."""
        barostat = ThermodynamicState._find_barostat(self.barostated_alanine)
        assert isinstance(barostat, openmm.MonteCarloBarostat)

        # Raise exception if multiple or unsupported barostats found
        TE = ThermodynamicsError  # shortcut
        test_cases = [(self.multiple_barostat_alanine, TE.MULTIPLE_BAROSTATS),
                      (self.unsupported_barostat_alanine, TE.UNSUPPORTED_BAROSTAT)]
        for system, err_code in test_cases:
            with nose.tools.assert_raises(ThermodynamicsError) as cm:
                ThermodynamicState._find_barostat(system)
            assert cm.exception.code == err_code

    def test_method_is_barostat_consistent(self):
        """ThermodynamicState._is_barostat_consistent() method."""
        temperature = 300*unit.kelvin
        pressure = 1.0*unit.atmosphere
        state = ThermodynamicState(self.barostated_alanine, temperature)

        barostat = openmm.MonteCarloBarostat(pressure, temperature)
        assert state._is_barostat_consistent(barostat)
        barostat = openmm.MonteCarloBarostat(pressure + 0.2*unit.atmosphere, temperature)
        assert not state._is_barostat_consistent(barostat)
        barostat = openmm.MonteCarloBarostat(pressure, temperature + 10*unit.kelvin)
        assert not state._is_barostat_consistent(barostat)

    def test_property_temperature(self):
        """ThermodynamicState.temperature property."""
        state = ThermodynamicState(self.barostated_alanine,
                                   self.temperature)
        assert state.temperature == self.temperature

        temperature = self.temperature + 10.0*unit.kelvin
        state.temperature = temperature
        assert state.temperature == temperature
        assert get_barostat_temperature(state._barostat) == temperature

    def test_property_pressure(self):
        """ThermodynamicState.pressure property."""
        # Vacuum and implicit system are read with no pressure
        nonperiodic_testcases = [self.toluene_vacuum, self.toluene_implicit]
        for system in nonperiodic_testcases:
            state = ThermodynamicState(system, self.temperature)
            assert state.pressure is None

            # We can't set the pressure on non-periodic systems
            with nose.tools.assert_raises(ThermodynamicsError) as cm:
                state.pressure = 1*unit.atmosphere
            assert cm.exception.code == ThermodynamicsError.BAROSTATED_NONPERIODIC

        # Correctly reads and set system pressures
        periodic_testcases = [self.alanine_explicit]
        for system in periodic_testcases:
            state = ThermodynamicState(system, self.temperature)
            assert state.pressure is None
            assert state._barostat is None

            # Setting pressure adds a barostat
            state.pressure = self.pressure
            assert state.pressure == self.pressure
            barostat = state._barostat
            assert barostat.getDefaultPressure() == self.pressure
            assert get_barostat_temperature(barostat) == self.temperature

            # Setting new pressure changes the barostat parameters
            new_pressure = self.pressure + 1.0*unit.atmosphere
            state.pressure = new_pressure
            assert state.pressure == new_pressure
            barostat = state._barostat
            assert barostat.getDefaultPressure() == new_pressure
            assert get_barostat_temperature(barostat) == self.temperature

            # Setting pressure to None removes barostat
            state.pressure = None
            assert state._barostat is None

    def test_property_volume(self):
        """Check that volume is computed correctly."""
        # For volume-fluctuating systems volume is None.
        state = ThermodynamicState(self.barostated_alanine, self.temperature)
        assert state.volume is None

        # For periodic systems in NVT, volume is correctly computed.
        system = self.alanine_explicit
        box_vectors = system.getDefaultPeriodicBoxVectors()
        volume = box_vectors[0][0] * box_vectors[1][1] * box_vectors[2][2]
        state = ThermodynamicState(system, self.temperature)
        assert state.volume == volume

        # For non-periodic systems, volume is None.
        state = ThermodynamicState(self.toluene_vacuum, self.temperature)
        assert state.volume is None

    def test_constructor_unsupported_barostat(self):
        """Exception is raised on construction with unsupported barostats."""
        TE = ThermodynamicsError  # shortcut
        test_cases = [(self.barostated_toluene, TE.BAROSTATED_NONPERIODIC),
                      (self.multiple_barostat_alanine, TE.MULTIPLE_BAROSTATS),
                      (self.unsupported_barostat_alanine, TE.UNSUPPORTED_BAROSTAT)]
        for i, (system, err_code) in enumerate(test_cases):
            with nose.tools.assert_raises(TE) as cm:
                ThermodynamicState(system=system, temperature=self.temperature)
            assert cm.exception.code == err_code

    def test_constructor_barostat(self):
        """The system barostat is properly configured on construction."""
        system = self.alanine_explicit
        old_serialization = openmm.XmlSerializer.serialize(system)
        assert ThermodynamicState._find_barostat(system) is None  # test-precondition

        # If we don't specify pressure, no barostat is added
        state = ThermodynamicState(system=system, temperature=self.temperature)
        assert state._barostat is None

        # If we specify pressure, barostat is added
        state = ThermodynamicState(system=system, temperature=self.temperature,
                                   pressure=self.pressure)
        assert state._barostat is not None

        # If we feed a barostat with an inconsistent temperature, it's fixed.
        barostat = openmm.MonteCarloBarostat(self.pressure,
                                             self.temperature + 1.0*unit.kelvin)
        copied_system = copy.deepcopy(system)
        copied_system.addForce(barostat)
        state = ThermodynamicState(copied_system, temperature=self.temperature)
        assert state._is_barostat_consistent(state._barostat)

        # If we feed a barostat with an inconsistent pressure, it's fixed.
        barostat = openmm.MonteCarloBarostat(self.pressure + 0.2*unit.atmosphere,
                                             self.temperature + 1.0*unit.kelvin)
        copied_system = copy.deepcopy(system)
        copied_system.addForce(barostat)
        state = ThermodynamicState(copied_system, temperature=self.temperature,
                                   pressure=self.pressure)
        assert state.pressure == self.pressure

        # The original system is unaltered.
        new_serialization = openmm.XmlSerializer.serialize(system)
        assert new_serialization == old_serialization

"""International Standard Atmosphere (ISA) and free-stream conditions.

All quantities are SI: metres, kelvin, pascal, kg/m^3, m/s.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

GAMMA = 1.4                 # ratio of specific heats for air
R_AIR = 287.05287           # specific gas constant, J/(kg K)
G0 = 9.80665                # standard gravity, m/s^2

# ISA layers: (base geopotential altitude m, base temperature K, lapse rate K/m)
_LAYERS = (
    (0.0, 288.15, -0.0065),
    (11000.0, 216.65, 0.0),
    (20000.0, 216.65, 0.001),
    (32000.0, 228.65, 0.0028),
    (47000.0, 270.65, 0.0),
    (51000.0, 270.65, -0.0028),
    (71000.0, 214.65, -0.002),
)
_TOP = 84852.0
P_SEA_LEVEL = 101325.0


def _layer_base_pressures():
    pressures = [P_SEA_LEVEL]
    for i in range(1, len(_LAYERS)):
        h0, t0, lapse = _LAYERS[i - 1]
        h1 = _LAYERS[i][0]
        pressures.append(_pressure_in_layer(pressures[-1], t0, lapse, h1 - h0))
    return pressures


def _pressure_in_layer(p0: float, t0: float, lapse: float, dh: float) -> float:
    if lapse == 0.0:
        return p0 * math.exp(-G0 * dh / (R_AIR * t0))
    t = t0 + lapse * dh
    return p0 * (t / t0) ** (-G0 / (lapse * R_AIR))


_BASE_P = _layer_base_pressures()


def sutherland_viscosity(temperature: float) -> float:
    """Dynamic viscosity of air (Pa s) from Sutherland's law."""
    return 1.458e-6 * temperature ** 1.5 / (temperature + 110.4)


@dataclass(frozen=True)
class Atmosphere:
    altitude: float      # m
    temperature: float   # K
    pressure: float      # Pa
    density: float       # kg/m^3
    speed_of_sound: float  # m/s
    viscosity: float     # Pa s

    @property
    def kinematic_viscosity(self) -> float:
        return self.viscosity / self.density


def isa(altitude: float, temperature_offset: float = 0.0) -> Atmosphere:
    """ISA conditions at a geopotential altitude (m), with optional ISA+dT offset (K)."""
    h = min(max(altitude, -1000.0), _TOP)
    idx = 0
    for i, layer in enumerate(_LAYERS):
        if h >= layer[0]:
            idx = i
    h0, t0, lapse = _LAYERS[idx]
    t_std = t0 + lapse * (h - h0)
    p = _pressure_in_layer(_BASE_P[idx], t0, lapse, h - h0)
    t = max(t_std + temperature_offset, 1.0)
    rho = p / (R_AIR * t)
    return Atmosphere(
        altitude=h,
        temperature=t,
        pressure=p,
        density=rho,
        speed_of_sound=math.sqrt(GAMMA * R_AIR * t),
        viscosity=sutherland_viscosity(t),
    )


@dataclass(frozen=True)
class Freestream:
    """Free-stream state far upstream of the model."""
    atmosphere: Atmosphere
    speed: float  # m/s

    @property
    def mach(self) -> float:
        return self.speed / self.atmosphere.speed_of_sound

    @property
    def dynamic_pressure(self) -> float:
        return 0.5 * self.atmosphere.density * self.speed ** 2

    @property
    def total_pressure(self) -> float:
        m2 = self.mach ** 2
        return self.atmosphere.pressure * (1 + 0.5 * (GAMMA - 1) * m2) ** (GAMMA / (GAMMA - 1))

    @property
    def total_temperature(self) -> float:
        return self.atmosphere.temperature * (1 + 0.5 * (GAMMA - 1) * self.mach ** 2)

    def reynolds(self, length: float) -> float:
        return self.speed * length / self.atmosphere.kinematic_viscosity

    @staticmethod
    def from_mach(atmosphere: Atmosphere, mach: float) -> "Freestream":
        return Freestream(atmosphere, mach * atmosphere.speed_of_sound)

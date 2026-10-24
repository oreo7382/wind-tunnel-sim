# Theory, assumptions and limits

This page lists the models behind every number in the simulator, the assumptions they make, and how
they were checked. Lengths are normalised by the chord (airfoils) or the frontal height (blunt bodies),
and velocities by the free-stream speed V∞, unless stated otherwise.

## 1. Atmosphere and free stream

* **International Standard Atmosphere** (7 layers, up to 84.9 km) with an optional ISA ± ΔT offset.
* Viscosity from **Sutherland's law**; speed of sound a = √(γRT) with γ = 1.4, R = 287.05 J/(kg K).
* Reynolds number Re = V c / ν, Mach number M = V / a, dynamic pressure q = ½ρV².

## 2. Two-dimensional airfoils

### 2.1 Inviscid flow: Hess-Smith panel method
The surface is split into N straight panels (160 by default, cosine-clustered at the leading and trailing
edges). Each panel carries a constant source strength qⱼ, and all panels share one constant vortex
strength γ. There are N + 1 equations:

* zero normal velocity at each panel midpoint, and
* the **Kutta condition**: equal tangential speeds on the two trailing-edge panels.

The system is solved once for a unit free stream along x and once along y. Any angle of attack is then a
superposition, which is why changing α is instant. The surface pressure coefficient is
Cp = 1 − (Vₜ / V∞)², and forces come from integrating the pressure. The same influence formulas give the
velocity anywhere in the field, and the streamlines are integrated from it with fourth-order Runge-Kutta.

*Checks:* the circular cylinder matches the exact Cp = 1 − 4 sin²θ to 10⁻¹⁴. NACA 0012 at 5° gives
cl = 0.60 (XFOIL inviscid 0.60). NACA 2412 gives α_L0 = −2.1° and Cm,c/4 = −0.054.

### 2.2 Boundary layer (viscous effects)
The boundary layer is marched along each surface from the stagnation point, using the inviscid edge
velocity uₑ (one-way coupling):

| Region | Method |
|---|---|
| Laminar | **Thwaites**: θ² = 0.45 ν / uₑ⁶ ∫ uₑ⁵ ds, with Cebeci-Bradshaw correlations for H and skin friction. Laminar separation is flagged when λ < −0.09. |
| Transition | **Michel's criterion** Re_θ > 1.174 (1 + 22400/Reₓ) Reₓ^0.46, a forced trip at a chosen x/c, laminar separation (short bubble), fully turbulent, or fully laminar |
| Turbulent | **Head's entrainment method** with the Ludwieg-Tillmann skin-friction law. Separation is assumed where H ≥ 2.0. |
| Drag | **Squire-Young** formula at the trailing edge: c_d = 2θ (uₑ)^((H+5)/2), summed over both surfaces |

The last 1.5 % of chord is skipped, because the inviscid solution has a stagnation singularity at a
closed trailing edge.

**Stall model.** When the turbulent layer separates at x_sep, lift is reduced with the **Kirchhoff /
Beddoes-Leishman** relation cl = cl,inv · ((1 + √f)/2)², with f = x_sep / c. The lost leading-edge
suction is added as a separation-drag term. The boundary layer's displacement thickness at the trailing
edge also slightly "decambers" the section; this is an empirical factor of at most 7 %, calibrated
against NACA lift slopes.

*Checks:* a flat plate is within 1 % of Blasius. NACA 0012 at Re = 3×10⁶ gives cd₀ ≈ 0.0066 (measured
0.0058–0.0065) and cl_max ≈ 1.46 at 15° (measured 1.5–1.6 at about 16°). Expect roughly ±15–20 % on cl_max
and on drag after stall.

### 2.3 Compressibility (subsonic)
The incompressible Cp is corrected point by point:

* Prandtl-Glauert: Cp = Cp₀ / β, with β = √(1 − M²)
* Kármán-Tsien: Cp = Cp₀ / [β + M² Cp₀ / (2(1 + β))]
* Laitone: Cp = Cp₀ / [β + M²(1 + (γ−1)M²/2) Cp₀ / (2β)]

**Critical Mach number:** the smallest M at which the corrected minimum Cp reaches
Cp* = 2/(γM²) {[(2 + (γ−1)M²)/(γ+1)]^(γ/(γ−1)) − 1}.

**Local state:** from the corrected Cp, the local static pressure is p = p∞ + q Cp. Isentropic relations
with the free-stream total pressure then give the local Mach number, temperature, density and speed.
(In incompressible mode the density and temperature stay constant and V = V∞ √(1 − Cp).)

Above the critical Mach number, a **wave drag** of 20 (M − M_cr)⁴ (Lock's 4th-power law) is added,
capped at 0.1. Inside supersonic pockets the correction is not valid, so the local Mach number is limited
to 1.6, where a real shock would end the pocket, and a warning is shown. Streamline *shapes* in compressible
flow are the incompressible ones; only the magnitudes are corrected.

For 0.95 < M < 1.08 the linear theories are singular, so they are evaluated at the edge of that band and
the app says so.

### 2.4 Supersonic flow (M > 1)
**Busemann's second-order theory**: Cp = C₁δ + C₂δ², with C₁ = 2/β and
C₂ = [(γ+1)M⁴ − 4β²] / (2β⁴), where β = √(M² − 1) and δ is the local flow deflection.

In the field, disturbances run unchanged along Mach lines (angle μ = asin(1/M)). A point above the
airfoil takes the deflection of the upper-surface point where its Mach line starts, and a point below
takes it from the lower surface. Pressure is clipped to the normal-shock stagnation value at blunt noses,
which really have a detached bow shock, and the app warns about this.

*Check:* a 6 % biconvex airfoil at Mach 2 and α = 2° gives cl = 0.0801 and cd = 0.0139; Ackeret's
linear theory gives 0.0806 and 0.0139.

## 3. Finite wings: vortex-lattice method
* Horseshoe vortices on n_span × n_chord panels: bound legs at the panel quarter chord, control points at
  three-quarter chord, trailing legs to infinity. Spanwise spacing is cosine, with control stations at
  the θ-midpoints (Lan's choice), which keeps the tip strips accurate.
* Camber enters through the section zero-lift angle. Twist is linear from root to tip, and dihedral
  tilts the panel normals.
* **Compressibility:** the Prandtl-Glauert/Göthert rule. Geometry is stretched by 1/β in x, and the
  resulting circulation is the compressible one.
* **Induced drag** comes from a Trefftz-plane analysis of the trailing vortex sheet, giving span
  efficiency e = CL² / (π AR CDi). Induced angle αᵢ = −w_Trefftz / 2.
* **Viscous correction (wing stall):** each strip's lift is iterated onto the airfoil's 2-D viscous polar
  by adjusting its effective incidence ("decambering" iteration). This captures where the wing stalls
  first. Profile drag is the strip integral of the section cd at each strip's effective angle.
* **Section view:** the 2-D analysis of the local chord at α_eff = α_geo + twist − αᵢ. For swept wings it
  uses the Mach number normal to the sweep line (simple sweep theory).
* Tip-vortex pictures trace streamlines through the vortex system, with a wake aligned to the free stream
  and a smoothed vortex core. The wake does not roll up, so the real vortex is tighter.

*Checks:* an elliptic loading gives uniform Trefftz downwash (−Γ₀/b) to 0.2 %. The lift slope is within
about 7 % of DATCOM/Helmbold, e ≤ 1 for all planforms, and the lift slope rises with Mach number as
Prandtl-Glauert predicts. The solver is subsonic only (M ≤ 0.95).

## 4. Propellers: blade-element momentum theory
For each blade element the inflow angle φ is found by bracketing and bisection on the residual
g(φ) − (V/Ωr) h(φ) = 0, where

g = sin φ − σ' Cₓ / (4F sin φ),  h = cos φ + σ' C_y / (4F sin φ),

σ' = Bc/(2πr), Cₓ = cl cos φ − cd sin φ and C_y = cl sin φ + cd cos φ. This form stays well-behaved at
zero flight speed (static thrust). F is the product of the Prandtl tip and hub loss factors. Section
cl and cd come from the airfoil's 2-D viscous polar at the 75 % radius Reynolds number, blended into
flat-plate behaviour beyond ±25°, with cl divided by β for the local Mach number.

Outputs are T, Q, P = QΩ, η = TV/P, CT = T/(ρn²D⁴), CP = P/(ρn³D⁵), J = V/(nD), the figure of merit
T^1.5 / (P √(2ρA)), and the momentum-theory induced velocity from T = 2ρA(V + v)v.

**Slipstream view (actuator disk):** the axial velocity along the axis is
u(x) = V + v (1 + x/√(x² + R²)). Stream tubes contract to conserve mass, and the total pressure jumps by
T/A across the disk.

*Checks:* a 1.8 m two-blade propeller at 2400 rpm gives η ≈ 0.80–0.90 in cruise, a static figure of merit
of about 0.67, and negative thrust (windmilling) at high advance ratio.

## 5. Blunt bodies
* The pressure field is potential flow: the panel method for 2-D shapes, with a prescribed circulation
  Γ = 2πa(ωa) for a spinning cylinder, and the exact solution for the sphere. Potential flow has no drag
  (d'Alembert's paradox), so **drag comes from measured data**:
  * circular cylinder: classic CD(Re) curve including the drag crisis near Re ≈ 3×10⁵;
  * sphere: Morrison's (2013) correlation;
  * other shapes: Hoerner / White tables (for example square 2.1, flat plate 2.0, ellipse 2:1 0.6 → 0.2
    after the crisis);
  * a compressibility factor based on cylinder data.
* Laminar separation comes from Thwaites' method on the potential-flow velocity: 104° for the cylinder,
  matching the classic textbook result. The real subcritical cylinder separates at about 80°, because its
  real pressure distribution differs.
* Magnus lift: potential flow gives cl = 2π·(ωa/V). A fit to measured data, cl ≈ s(1 − e^(−1.5s)), is
  reported as the realistic value.
* Strouhal numbers are about 0.21 for the cylinder, 0.19 for the sphere and 0.13–0.18 for sharp-edged
  shapes, giving shedding frequency f = St V / d.

## 6. What the simulator does **not** do
* It does not solve the Navier-Stokes equations (no CFD). Separated wakes, shock positions, shock-induced
  separation and post-stall flow are estimates.
* There is no viscous-inviscid interaction: the boundary layer does not modify the pressure field.
* Transonic flow (roughly 0.95 < M < 1.1) has no reliable linear theory.
* 3-D compressibility is Prandtl-Glauert only, so the VLM has no transonic or supersonic wings.
* Propeller blades are assumed to have constant-pitch helical twist and linear chord taper, and there is
  no blade-vortex interaction or noise model.

Use the results to build intuition and to check trends. Use wind-tunnel data or validated CFD for
anything that matters.

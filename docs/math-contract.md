# Exact mathematical contract — 0.2.0

This backend handles a fixed finite-dimensional unital associative algebra A,
vector space V, and bilinear map B: A × A → V over the rationals. The unknowns
are left and right action matrices L_i, R_i. It does not infer missing tensors.
A has dimension at most 4; V at most 3. Scalars are reduced integer numerator /
positive denominator objects, never floats. Resource limits can stop a run;
that is not a mathematical rejection.

## Reconstruction and supplied-point validation

For basis vectors a,b,c, the weak equations are

```
B(ab,c) = L(a) B(b,c) + R(b) B(a,c)
B(a,bc) = R(c) B(a,b) + L(b) B(a,c).
```

The compiler constructs M u = r. A consistent certificate includes a particular
solution and a complete independent kernel basis. An inconsistent certificate
includes λ with λᵀ M = 0 and λᵀ r ≠ 0. The verifier regenerates M,r, checks all
identities and computes rank independently.

A supplied strict point must also satisfy

```
L(1) = R(1) = I
L(a)L(b) = L(ab)
R(b)R(a) = R(ab)
L(a)R(b) = R(b)L(a).
```

Validation certifies or rejects that point only. There is no strict-fiber search
and no conclusion that all strict solutions are absent when a candidate fails.

## Tangent and primary order-two obstruction

Let F be the concatenated weak, unit, left, right and mixed residuals, and p a
verified strict point. F has degree at most two. The compiler produces the exact
linear tangent operator D_p and a complete basis of its kernel. A zero kernel
means infinitesimal rigidity at p; it does not prove global uniqueness.

For one supplied ξ, first check D_p ξ = 0. A non-tangent direction produces
NOT_TANGENT_VECTOR with its nonzero residual, not an obstruction certificate.
For a tangent direction, define Q_p(ξ) by

```
F(p + t ξ + t² η) = t D_p ξ + t² (D_p η + Q_p(ξ)) mod t³.
```

Success provides η with D_p η + Q_p(ξ) = 0. Rejection provides λ with
λᵀ D_p = 0 and λᵀ Q_p(ξ) ≠ 0, ruling out every second-order correction for this
ξ at this p. It does not rule out other points or directions. Success certifies
extension modulo t³ only, not higher orders or convergence.

The independent verifier imports no compiler or solver. It regenerates F,
uses exact polarization to reconstruct D_p and Q_p, checks the full tangent
basis via its own rank routine, and checks the correction or dual identities.
Compiler and verifier share typed input / serialization utilities; this is
independent executable replay, not proof-assistant formalization or an
implementation-independent trusted computing base.

## Certificate and provenance boundary

Every certificate binds canonical input SHA-256, schema, backend,
compiler version, scope, provenance, result and its own digest. A digest alone
is not proof: replay verifies witness mathematics. Unsupported or malformed
input, resource exhaustion and infrastructure failure remain separate outcomes.
Legacy 0.1.0 Pass 1–4 certificates can replay; new tangent/order-two certificates
require 0.2.0. The general backend is `general_fd_v1`, schema `1`.

The software began after August 31, 2026. Earlier mathematical research informed
its specification; it is not claimed as newly proved by this application.
The two dual-number demonstration fixtures are newly constructed exact examples,
not recovered historical cubic artifacts. Missing fixed-cubic source artifacts
remain unsupported. No strict solver, Pass 7–10, third-order obstruction,
fixed-cubic normal form or complete Hochschild cochain engine is implemented.
The private handoff specification's SHA-256 is
`29d516d0bfc12dc17ee8f29ac842d939910db73dcfbd4c303a735d21719da4e6`;
its text is not redistributed in this source release. The executable contract,
fixtures and tests in this repository suffice to reproduce the claims above.

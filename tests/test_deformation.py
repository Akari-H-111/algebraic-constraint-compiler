"""Exact order-two regressions, independent polynomial substitution and forgeries."""

import copy
from fractions import Fraction
from pathlib import Path
import unittest
from unittest.mock import patch

from algebraic_compiler.compiler import compile_reconstruction
from algebraic_compiler.ir import PASSES, digest, load_json, parse_problem, rational, scalar, vector
from algebraic_compiler.verifier import verify_certificate, verify_run
from test_algebraic import problem

ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return load_json((ROOT / 'examples/algebraic' / (name + '.json')).read_bytes())


def rehash(cert):
    cert['certificate_sha256'] = digest({k: v for k, v in cert.items() if k != 'certificate_sha256'})
    return cert


def assert_polynomial_lift(test, raw, lift):
    """Direct substitution p+t*xi+t²*eta into every identity modulo t³."""
    p = parse_problem(raw)
    coefficients = [p.strict_point, p.tangent_direction, tuple(rational(q) for q in lift)]
    def entry(order, side, i, r, c):
        return coefficients[order][p.variable_index(side, i, r, c)]
    def product(order, side_a, i, side_b, j, r, c):
        return sum(entry(a, side_a, i, r, k) * entry(order-a, side_b, j, k, c)
                   for a in range(order+1) for k in range(p.d))
    for order in range(3):
        for side in range(2):
            for r in range(p.d):
                for c in range(p.d):
                    test.assertEqual(sum(p.unit[i]*entry(order, side, i, r, c) for i in range(p.m)),
                                     int(order == 0 and r == c))
        for i in range(p.m):
            for j in range(p.m):
                for r in range(p.d):
                    for c in range(p.d):
                        test.assertEqual(product(order, 0, i, 0, j, r, c),
                                         sum(p.multiplication[i][j][k]*entry(order, 0, k, r, c) for k in range(p.m)))
                        test.assertEqual(product(order, 1, j, 1, i, r, c),
                                         sum(p.multiplication[i][j][k]*entry(order, 1, k, r, c) for k in range(p.m)))
                        test.assertEqual(product(order, 0, i, 1, j, r, c), product(order, 1, j, 0, i, r, c))
                for k in range(p.m):
                    for r in range(p.d):
                        left = sum(entry(order, 0, i, r, b)*p.bilinear[j][k][b] +
                                   entry(order, 1, j, r, b)*p.bilinear[i][k][b] for b in range(p.d))
                        right = sum(entry(order, 1, k, r, b)*p.bilinear[i][j][b] +
                                    entry(order, 0, j, r, b)*p.bilinear[i][k][b] for b in range(p.d))
                        test.assertEqual(left, sum(p.multiplication[i][j][s]*p.bilinear[s][k][r] for s in range(p.m)) if order == 0 else 0)
                        test.assertEqual(right, sum(p.multiplication[j][k][s]*p.bilinear[i][s][r] for s in range(p.m)) if order == 0 else 0)


class DeformationTests(unittest.TestCase):
    def test_false_tangent_and_nonzero_correction(self):
        bad = compile_reconstruction(fixture('second-order-obstructed'))
        self.assertEqual(bad['mathematical_status'], 'obstruction.nonzero')
        self.assertEqual(bad['certificates'][-2]['result']['dimension'], 1)
        self.assertEqual(bad['certificates'][-2]['result']['basis'], [vector([0, -1, 0, 1])])
        self.assertTrue(all(q['num'] == 0 for q in bad['result']['tangent_residuals']))
        self.assertEqual(bad['result']['nonmembership_witness']['pairing'], scalar(1))
        self.assertTrue(verify_run(bad)['certificate_verified'])
        raw = fixture('second-order-extends')
        good = compile_reconstruction(raw)
        self.assertEqual(good['mathematical_status'], 'obstruction.vanishes')
        self.assertEqual(good['result']['lift_witness'], vector([0]*6 + [-1] + [0]*9))
        assert_polynomial_lift(self, raw, good['result']['lift_witness'])
        self.assertEqual(good, compile_reconstruction(raw))
        self.assertTrue(verify_run(good)['certificate_verified'])

    def test_noncommutative_conjugation_direction(self):
        raw = problem(m=3, d=3, passes=list(PASSES))
        products = ((0,0,0),(0,1,1),(0,2,2),(1,0,1),(2,0,2),(1,2,1),(2,2,2))
        raw['algebra']['multiplication'] = [{'i':i,'j':j,'k':k,'value':scalar(1)} for i,j,k in products]
        lookup = set(products)
        coordinates = [Fraction(int(((i,c,r) if side == 0 else (c,i,r)) in lookup))
                       for side in range(2) for i in range(3) for r in range(3) for c in range(3)]
        raw["strict_point"] = {"schema_version":"1", "coordinate_system":"action_entries", "coordinates":vector(coordinates)}
        # Simultaneous conjugation preserves left, opposite-right and mixed laws.
        s = [[Fraction(int(r == 2 and c == 0)) for c in range(3)] for r in range(3)]
        direction = []
        for offset in range(0, len(coordinates), 9):
            a = [coordinates[offset+r*3:offset+r*3+3] for r in range(3)]
            direction.extend(sum(s[r][k]*a[k][c] - a[r][k]*s[k][c] for k in range(3)) for r in range(3) for c in range(3))
        raw['tangent_direction'] = {'schema_version':'1','coordinate_system':'action_entries','coordinates':vector(direction)}
        run = compile_reconstruction(raw)
        self.assertEqual(run['mathematical_status'], 'obstruction.vanishes', run)
        assert_polynomial_lift(self, raw, run['result']['lift_witness'])
        self.assertTrue(verify_run(run)['certificate_verified'])

    def test_preconditions_and_local_rigidity(self):
        raw = fixture('strict-point-valid'); raw['requested_passes'].append('tangent')
        run = compile_reconstruction(raw)
        self.assertEqual(run['result']['dimension'], 0)
        self.assertEqual(run['result']['rigidity'], 'infinitesimally_rigid')
        raw = fixture('second-order-obstructed')
        raw['tangent_direction']['coordinates'][0] = scalar(1)
        run = compile_reconstruction(raw)
        self.assertEqual(run['code'], 'NOT_TANGENT_VECTOR')
        self.assertIsNone(run['result']['source_vector'])
        self.assertTrue(verify_run(run)['certificate_verified'])
        raw['strict_point']['coordinates'][0] = scalar(0)
        run = compile_reconstruction(raw)
        self.assertEqual(run['code'], 'STRICT_POINT_INVALID')
        self.assertNotIn('tangent', run['verified_through'])
        for field in ('strict_point','tangent_direction'):
            raw = fixture('second-order-obstructed'); del raw[field]
            self.assertEqual(compile_reconstruction(raw)['code'], 'INVALID_SCHEMA')

    def test_rehashed_tangent_and_obstruction_forgeries(self):
        raw = fixture('second-order-obstructed'); run = compile_reconstruction(raw)
        tangent = run['certificates'][-2]
        mutations = [('rank',0), ('dimension',True), ('basis',[]), ('basis',[vector([0]*4)]),
                     ('point_sha256','0'*64), ('operator',{})]
        for key,value in mutations:
            cert = copy.deepcopy(tangent); cert['result'][key] = value
            self.assertFalse(verify_certificate(rehash(cert), raw)['certificate_verified'], key)
        cert = run['certificates'][-1]
        for key,value in [('direction_sha256','0'*64), ('tangent_operator_sha256','0'*64),
                          ('source_vector',vector([0]*30)), ('tangent_residuals',vector([1]*30)),
                          ('order',True), ('lift_witness',vector([0]*4))]:
            altered = copy.deepcopy(cert); altered['result'][key] = value
            self.assertFalse(verify_certificate(rehash(altered), raw)['certificate_verified'], key)
        for key in ('pairing','left_null_vector'):
            altered = copy.deepcopy(cert)
            altered['result']['nonmembership_witness'][key] = scalar(0) if key == 'pairing' else vector([0]*30)
            self.assertFalse(verify_certificate(rehash(altered), raw)['certificate_verified'])
        raw = fixture('second-order-extends'); altered = compile_reconstruction(raw)['certificates'][-1]
        altered['result']['lift_witness'] = vector([0]*16)
        self.assertFalse(verify_certificate(rehash(altered), raw)['certificate_verified'])

    def test_independent_replay_and_legacy_version(self):
        run = compile_reconstruction(fixture('second-order-extends'))
        with patch('algebraic_compiler.compiler.weak', side_effect=RuntimeError('No producer')), \
             patch('algebraic_compiler.compiler.strict', side_effect=RuntimeError('No producer')), \
             patch('algebraic_compiler.deformation.tangent', side_effect=RuntimeError('No producer')), \
             patch('algebraic_compiler.deformation.obstruction', side_effect=RuntimeError('No producer')):
            self.assertTrue(verify_run(run)['certificate_verified'])
        raw = fixture('strict-point-valid'); legacy = compile_reconstruction(raw)
        for cert in legacy['certificates']:
            cert['compiler_version'] = '0.1.0'; rehash(cert)
        self.assertTrue(verify_run(legacy)['certificate_verified'])
        cert = run['certificates'][-1]; cert['compiler_version'] = '0.1.0'
        self.assertFalse(verify_certificate(rehash(cert), run['problem'])['certificate_verified'])

    def test_resource_failure_is_incomplete(self):
        raw = fixture('second-order-obstructed')
        with patch('algebraic_compiler.verifier.time.monotonic', side_effect=range(0, 10000, 20)):
            run = compile_reconstruction(raw)
        self.assertEqual(run['status'], 'INCOMPLETE_RESOURCE_LIMIT')
        self.assertFalse(run['certificate_verified'])
        self.assertNotEqual(run['mathematical_status'], 'obstruction.nonzero')


if __name__ == '__main__':
    unittest.main()

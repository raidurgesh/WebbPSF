import sys, os
import numpy as np
import matplotlib.pyplot as plt
import astropy.io.fits as fits

import logging

_log = logging.getLogger('test_webbpsf')
_log.addHandler(logging.NullHandler())

from .. import webbpsf_core
import poppy


# ------------------    FGS Tests    ----------------------------

from .test_webbpsf import generic_output_test, do_test_source_offset, do_test_set_position_from_siaf

test_fgs = lambda: generic_output_test('FGS')
test_fgs_source_offset_00 = lambda: do_test_source_offset('FGS', theta=0.0, monochromatic=2.5e-6)
test_fgs_source_offset_45 = lambda: do_test_source_offset('FGS', theta=45.0, monochromatic=2.5e-6)

test_fgs_set_siaf = lambda: do_test_set_position_from_siaf(
    'FGS', ['FGS1_FP1MIMF', 'FGS2_SUB128CNTR', 'FGS1_SUB128LL', 'FGS2_SUB32DIAG']
)

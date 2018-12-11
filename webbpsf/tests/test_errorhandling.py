# This file contains code for testing various error handlers and user interface edge cases,
# as opposed to testing the main body of functionality of the code.

import sys, os
import os.path
import logging
_log = logging.getLogger('test_webbpsf')
_log.addHandler(logging.NullHandler())

import numpy as np
import matplotlib.pyplot as plt
import astropy.io.fits as fits
import pytest

import poppy
from .. import webbpsf_core
from .. import utils
from .. import conf


def _exception_message_starts_with(excinfo, message_body):
    return excinfo.value.args[0].startswith(message_body)


def test_calc_psf_catch_incompatible_oversampling():
    """Test that supplying all three oversampling arguments raises a ValueError"""
    nc = webbpsf_core.Instrument('NIRCam')
    nc.pupilopd=None
    nc.filter='F212N'

    with pytest.raises(ValueError) as excinfo:
        psf = nc.calc_psf(oversample=2, detector_oversample=10, fft_oversample=4)
    assert _exception_message_starts_with(excinfo, "You cannot specify")


def test_invalid_masks():
    nc = webbpsf_core.NIRCam()

    # first, test case indepedencence. These are all converted to upper case internally & automatically
    nc.image_mask = 'maskswb'
    assert nc.image_mask =='MASKSWB'

    with pytest.raises(ValueError) as excinfo:
        nc.image_mask = 'junk'
    assert _exception_message_starts_with(excinfo,"Instrument NIRCam doesn't have an image mask called 'JUNK'.")

    # now repeat for pupil masks:
    nc.pupil_mask = 'circlyot'
    assert nc.pupil_mask =='CIRCLYOT'

    with pytest.raises(ValueError) as excinfo:
        nc.pupil_mask = 'junk'
    assert _exception_message_starts_with(excinfo, "Instrument NIRCam doesn't have a pupil mask called 'JUNK'.")


def test_get_webbpsf_data_path_invalid(monkeypatch):
    real_env_webbpsf_path = os.getenv('WEBBPSF_PATH')
    real_conf_webbpsf_path = conf.WEBBPSF_PATH
    real_webbpsf_path = real_env_webbpsf_path or real_conf_webbpsf_path

    # Ensure get_webbpsf_data_path tests for environment variable when
    # config says to (and env var has been unset)
    monkeypatch.delenv('WEBBPSF_PATH')
    monkeypatch.setattr(
        conf,
        'WEBBPSF_PATH',
        'from_environment_variable'
    )
    with pytest.raises(EnvironmentError) as excinfo:
        _ = utils.get_webbpsf_data_path()
    assert _exception_message_starts_with(excinfo, 'Environment variable $WEBBPSF_PATH is not set!')

    # Test that we can override the WEBBPSF_PATH setting here through
    # the config object even though the environment var is deleted
    # (n.b. get_webbpsf_data_path *does* ensure that the path is a
    # valid directory path, so we just use the parent of the real path)
    parent_of_webbpsf_path = os.path.abspath(os.path.join(real_webbpsf_path, '..'))
    monkeypatch.setattr(
        conf,
        'WEBBPSF_PATH',
        parent_of_webbpsf_path
    )

    assert utils.get_webbpsf_data_path() == parent_of_webbpsf_path

    # Test that we get an error if we make the path invalid
    monkeypatch.setattr(
        conf,
        'WEBBPSF_PATH',
        'some junk'
    )
    with pytest.raises(IOError) as excinfo:
        _ = utils.get_webbpsf_data_path()
    assert _exception_message_starts_with(excinfo, 'WEBBPSF_PATH ({}) is not a valid directory path!'.format('some junk'))

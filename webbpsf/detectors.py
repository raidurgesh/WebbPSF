import copy

import os
import numpy as np
import scipy
import webbpsf
from webbpsf import utils, constants
from astropy.convolution.kernels import CustomKernel
from astropy.convolution import convolve
from astropy.io import fits
import astropy.convolution
import scipy.signal as signal


def get_detector_ipc_model(inst, header):
    """Retrieve detector interpixel capacitance model

    The details of the available calibration data vary per instrument.

    Parameters:
    -----------
    inst : string
        instrument name
    header : astropy.io.fits.Header
        FITS header

    Returns:
    --------
    kernel : numpy.ndarray
        Convolution kernel
    meta : dict
        Metadata about that kernel to be saved in FITS header

    """

    inst = inst.upper()
    det = header['DET_NAME']  # detector name

    meta = dict()

    if inst == 'NIRCAM':
        det2sca = {
            'NRCA1': '481',
            'NRCA2': '482',
            'NRCA3': '483',
            'NRCA4': '484',
            'NRCA5': '485',
            'NRCB1': '486',
            'NRCB2': '487',
            'NRCB3': '488',
            'NRCB4': '489',
            'NRCB5': '490',
        }

        webbpsf.webbpsf_core._log.info(f'Detector IPC: NIRCam {det} (added)')
        # IPC effect
        # read the SCA extension for the detector
        sca_path = os.path.join(utils.get_webbpsf_data_path(), 'NIRCam', 'IPC', 'KERNEL_IPC_CUBE.fits')
        kernel_ipc = CustomKernel(fits.open(sca_path)[det2sca[det]].data[0])  # we read the first slice in the cube

        # PPC effect
        # read the SCA extension for the detector
        ## TODO: This depends on detector coordinates, and which readout amplifier. if in subarray, then the PPC effect is always like in amplifier 1
        sca_path_ppc = os.path.join(utils.get_webbpsf_data_path(), 'NIRCam', 'IPC', 'KERNEL_PPC_CUBE.fits')
        kernel_ppc = CustomKernel(fits.open(sca_path_ppc)[det2sca[det]].data[0])  # we read the first slice in the cube

        kernel = (kernel_ipc, kernel_ppc)  # Return two distinct convolution kernels in this case

        meta['IPCINST'] = ('NIRCam', 'Interpixel capacitance (IPC)')
        meta['IPCTYPA'] = (det2sca[det], 'NRC SCA num used for IPC and PPC model')
        meta['IPCFILE'] = (os.path.basename(sca_path), 'IPC model source file')
        meta['PPCFILE'] = (os.path.basename(sca_path_ppc), 'PPC model source file')

    elif inst == 'MIRI':
        webbpsf.webbpsf_core._log.info('Detector IPC: MIRI')

        alpha = webbpsf.constants.INSTRUMENT_IPC_DEFAULT_KERNEL_PARAMETERS[inst][0]
        beta = webbpsf.constants.INSTRUMENT_IPC_DEFAULT_KERNEL_PARAMETERS[inst][1]
        c = webbpsf.constants.INSTRUMENT_IPC_DEFAULT_KERNEL_PARAMETERS[inst][2]  # real observation noise adjustment
        miri_kernel = np.array([[c, beta, c], [alpha, 1 - 2 * alpha - 2 * beta - 4 * c, alpha], [c, beta, c]])
        kernel = CustomKernel(miri_kernel)

        meta['IPCINST'] = ('MIRI', 'Interpixel capacitance (IPC)')
        meta['IPCTYPA'] = (alpha, 'coupling coefficient alpha')
        meta['IPCTYPB'] = (beta, 'coupling coefficient beta')
        meta['IPCFILE'] = ('webbpsf.constants', 'IPC model source file')

    elif inst == 'NIRISS':
        # NIRISS IPC files distinguish between the 4 detector readout channels, and
        # whether or not the pixel is within the region of a large detector epoxy void
        # that is present in the NIRISS detector.

        # this set-up the input variables as required by Kevin Volk IPC code
        # image = psf_hdulist[ext].data
        xposition = header['DET_X']
        yposition = header['DET_Y']
        # find the voidmask fits file
        voidmask10 = os.path.join(utils.get_webbpsf_data_path(), 'NIRISS', 'IPC', 'voidmask10.fits')

        if os.path.exists(voidmask10):
            maskimage = fits.getdata(voidmask10)
        else:
            maskimage = None
            webbpsf.webbpsf_core._log.info('Error reading the file voidmask10.fits.  Will assume a non-void position.')

        nchannel = int(yposition) // 512
        try:
            flag = maskimage[nchannel, int(xposition)]
        except IndexError:
            # This marks the pixel as non-void by default if the maskimage is not
            # read in properly
            flag = 0
        frag1 = ['A', 'B', 'C', 'D']
        frag2 = ['notvoid', 'void']

        ipcname = 'ipc5by5median_amp' + frag1[nchannel] + '_' + frag2[flag] + '.fits'
        ipc_file = os.path.join(utils.get_webbpsf_data_path(), 'NIRISS', 'IPC', ipcname)
        if os.path.exists(ipc_file):
            kernel = fits.getdata(ipc_file)
            # newimage = signal.fftconvolve(image, ipckernel, mode='same')
            meta['IPCINST'] = ('NIRISS', 'Interpixel capacitance (IPC)')
            meta['IPCTYPA'] = (ipcname, 'kernel file used for IPC correction')
            meta['IPCFILE'] = (os.path.basename(ipc_file), 'IPC model source file')
        else:
            kernel = None

            meta['IPCINST'] = ('NIRISS', 'Interpixel capacitance (IPC)')
            meta['IPCTYPA'] = ('NIRISS', 'No kernel file found')
            meta['IPCTYPB'] = ('NIRISS', 'No IPC correction applied')
            meta['IPCFILE'] = ('Not found', 'IPC model source file')
            webbpsf.webbpsf_core._log.info(f'NIRISS IPC kernel file {ipc_file} not found.')

    elif inst in ['FGS', 'NIRSPEC', 'WFI']:
        kernel = None  # No IPC models yet implemented for these
        meta['IPCFILE'] = ('Not found', 'IPC model source file')

    return kernel, meta


def apply_detector_ipc(psf_hdulist, extname='DET_DIST'):
    """Apply a model for interpixel capacitance


    NIRCam: IPC and PPC values derived during ground I&T, primarily ISIM-CV3 from Jarron Leisenring
    these IPC/PPC kernels will be update after flight values are available.
    For NIRCam only PPC effects are also included, these are relatively small compared to the IPC contribution
    MIRI: Convolution kernels from JWST-STScI-002925 by Mike Engesser
    NIRISS: Convolution kernels and base code provided by Kevin Volk
    The IPC kernel files are derived from IPC measurements made from NIRISS commissioning dark ramps by Chris Willott.

    For NIRISS the user needs to have the right kernels under $WEBBPSF_PATH/NIRISS/IPC/
    These kernels should be available with webbpsf data > Version 1.1.1

    You can turn On/Off IPC effects as an option.
     For example: inst.option['add_ipc'] = False, where inst is the instrument class. Default is True.


    Parameters
    ----------
    psf_hdulist : astropy.io.fits.HDUList
        A HDUList containing a webbpsf simulation result
    extname : string
        Which extension name to apply this to. This gets a bit tricky. In the normal calc_psf code path, this
        is applied to detector-sampled data, *after* binning the oversampled data to detector resolution. This
        is most intuitive, and in some sense better represents the actual physics of this effect. However in the
        psf_grid code path for making ePSFs, we need to be able to apply this model to oversampled PSFs.

    """

    # In cases for which the user has asked for the IPC to be applied to a not-present extension, we have nothing to add this to
    if extname not in psf_hdulist:
        webbpsf.webbpsf_core._log.debug(f'Skipping IPC simulation since ext {extname} is not found')
        return

    # This avoid applying IPC effect simulations twice
    keyword = 'IPCINST'
    if keyword in psf_hdulist[extname].header._keyword_indices:
        return

    inst = psf_hdulist[extname].header['INSTRUME'].upper()
    oversample = psf_hdulist[extname].header['OVERSAMP']

    kernel, meta = get_detector_ipc_model(inst, psf_hdulist[extname].header)
    if kernel is not None:
        if inst.upper() == 'NIRCAM':
            # For NIRCam we have distinct models for IPC and PPC effects. Needs two convolutions.
            ipckernel, ppckernel = kernel

            if oversample != 1:
                ipckernel = oversample_ipc_model(ipckernel, oversample)
                ppckernel = oversample_ipc_model(ppckernel, oversample)

            out_ipc_0 = convolve(psf_hdulist[extname].data, ipckernel)
            out_ipc = convolve(out_ipc_0, ppckernel)
        elif inst.upper() == 'NIRISS':
            # the NIRISS code provided by Kevin Volk was developed for a different convolution function
            if oversample != 1:
                kernel = oversample_ipc_model(kernel, oversample)
            out_ipc = signal.fftconvolve(psf_hdulist[extname].data, kernel, mode='same')
        else:
            if oversample != 1:
                kernel = oversample_ipc_model(kernel, oversample)
            out_ipc = convolve(psf_hdulist[extname].data, kernel)

        # apply kernel to DET_DIST
        psf_hdulist[extname].data = out_ipc

        # save metadata to header
        for key in meta:
            psf_hdulist[extname].header[key] = meta[key]
        psf_hdulist[extname].header.add_history('Applied detector interpixel capacitance (IPC) model')

    else:
        webbpsf.webbpsf_core._log.info('IPC corrections are not implemented yet for {}'.format(inst))
        psf_hdulist[extname].header['IPCINST'] = (inst, 'No IPC correction applied')

    return psf_hdulist


def apply_detector_charge_diffusion(psf_hdulist, options):
    """Apply a model for charge diffusion of photoelectrons within an H2RG
    This is a PLACEHOLDER, temporary heuristic

    """

    sigma = options.get('charge_diffusion_sigma')

    if sigma is None:
        # look up default from constants
        inst = psf_hdulist[0].header['INSTRUME'].upper()
        key = f"NIRCAM_{psf_hdulist[0].header['CHANNEL'][0]}W" if inst == 'NIRCAM' else inst
        sigma = webbpsf.constants.INSTRUMENT_DETECTOR_CHARGE_DIFFUSION_DEFAULT_PARAMETERS[key]

    ext = 1  # Apply to the 'OVERDIST' extension

    webbpsf.webbpsf_core._log.info(
        'Detector charge diffusion: Convolving with Gaussian with sigma={0:.3f} arcsec'.format(sigma)
    )
    out = scipy.ndimage.gaussian_filter(psf_hdulist[ext].data, sigma / psf_hdulist[0].header['PIXELSCL'])
    psf_hdulist[ext].header.add_history('Applied detector charge diffusion model.')
    psf_hdulist[ext].header['CHDFTYPE'] = ('gaussian', 'Type of detector charge diffusion model')
    psf_hdulist[ext].header['CHDFSIGM'] = (sigma, '[arcsec] Gaussian sigma for charge diff model')
    psf_hdulist[ext].data = out

    return psf_hdulist


def oversample_ipc_model(kernel, oversample):
    """Transform an IPC model convolution kernel to be applied to oversampled data.

    The correct way to do this turns out to be to intersperse zeros into the array, turning it
    into a sparse comb function. This is because the IPC is a discrete effect that acts on pixels,
    rather than a continuous function.

    (This is non-intuitive but mathematically yields precisely consistent results for either order
    of binning then applying IPC, or applying IPC then binning).

    Parameters
    ----------
    kernel : numpy.ndarray
        Convolution kernel for IPC model
    oversample : int
        Oversampling factor

    Returns a version of the kernel resampled and padded for use on oversampled data, for instance an ePSF

    """

    oversampling_kernel = np.zeros((oversample, oversample))
    oversampling_kernel[(oversample - 1) // 2, (oversample - 1) // 2] = 1

    kernel_oversample = np.kron(kernel, oversampling_kernel)

    if oversample % 2 == 0:
        # pad with an extra row and column of zeros, to convert into a symmetrical and odd-sized kernel
        npix = kernel_oversample.shape[0]
        padded_kernel = np.zeros((npix + 1, npix + 1))
        padded_kernel[1:, 1:] = kernel_oversample
        kernel_oversample = padded_kernel

    return kernel_oversample


# Functions for applying MIRI Detector Scattering Effect

# Lookup tables of shifts of the cruciform, estimated roughly from F560W ePSFs (ePSFs by Libralatto, shift estimate by Perrin)
cruciform_xshifts = scipy.interpolate.interp1d([0, 357, 1031], [1.5,0.5,-0.9], kind='linear', fill_value='extrapolate')
cruciform_yshifts = scipy.interpolate.interp1d([0, 511, 1031], [1.6,0,-1.6], kind='linear', fill_value='extrapolate')

def _make_miri_scattering_kernel_2d(in_psf, kernel_amp, oversample=1, wavelength=5.5, detector_position=(0, 0)):
    """Improved / more complex model of the MIRI cruciform, with parameterization to model
    additional features as seen in the GimMIRI models of Gaspar et al. 2021, PASP 133
    See in particular their Figure 12.

    Note, this contains a moderate amount of ad-hoc parameter fitting for scale factors to match observed PSFs from flight.

    Parameters
    ----------
    in_psf : ndarray
        PSF array for which to make the kernel
    kernel_amp : float
        Amplitude scale factor of the kernel
    oversample : int
        Amount by which the input PSF is oversampled
    wavelength : float
        Wavelength in microns, for use in adding wavelength-dependent effects
    detector_position : tuple of floats
        X, Y position, for use in adding wavelength-dependent effects

    """
    # make output array
    npix = in_psf.shape[0]
    cen = (npix-1) // 2
    kernel_2d = np.zeros( (npix, npix), float)

    ### make 1d kernels for the main cruciform bright lines 
    # Compute 1d indices
    x = np.arange(npix, dtype=float)
    x -= (npix-1)/2
    x /= oversample
    y = x  # we're working in 1d in this part, but clarify let's have separate coords for each axis

    # Create 1d kernels
    kernel_x = kernel_amp * np.exp(-np.abs(x) / 25)
    kernel_y = kernel_amp * np.exp(-np.abs(x) / 25)

    # reduce intensity in the inner part, since the cruciform is suppressed at small radii
    kernel_x[np.abs(x) < constants.MIRI_CRUCIFORM_INNER_RADIUS_PIX] *= 0.5
    kernel_y[np.abs(y) < constants.MIRI_CRUCIFORM_INNER_RADIUS_PIX] *= 0.5

    # add in the extra diffraction peaks into each 1d kernel, as seen in Gaspar et al. 2021
    # but first, save the total flux for normalization later
    normfactor = kernel_x.sum()

    # Empirically, the 'center' of the cruciform shifts inwards towards the center of the detector
    # i.e. for the upper right corner, the cruciform shifts down and left a bit, etc. 
    # Turns out the centers of the peaks in each cruciform arm also seem to shift a bit, so
    # let's use the same shifts to try to model those, with some scale factor
    yshift = cruciform_yshifts(detector_position[1])
    xshift = cruciform_xshifts(detector_position[0])

    for loc, amp in zip(constants.MIRI_CRUCIFORM_PEAKS_LOC, constants.MIRI_CRUCIFORM_PEAKS_AMP):
        # Empirically, the locations of the peaks shift slightly around the FOV, in the opposite sign as the cruciform itself shifts
        # we model this ad hoc based on comparisons iwth the ePSF data.
        # The scale factors here are a bit of a handwave by eye, not yet a rigorous fit...
        scaled_loc_x = loc * wavelength / constants.MIRI_CRUCIFORM_PEAK_REFWAVE * 1.1
        scaled_loc_y = loc * wavelength / constants.MIRI_CRUCIFORM_PEAK_REFWAVE * 1.1

        scaled_amp = amp * constants.MIRI_CRUCIFORM_PEAKS_AMP_ADJUST  # ad hoc, to make it work; basically a units scale factor from the plot in Andras' paper 
        peak_x = scaled_amp * np.exp(-(x-scaled_loc_x + xshift/2)**2) + scaled_amp * np.exp(-(x+scaled_loc_x + xshift/2)**2)
        peak_y = scaled_amp * np.exp(-(y-scaled_loc_y + yshift/2)**2) + scaled_amp * np.exp(-(y+scaled_loc_y + yshift/2)**2)
        kernel_x += peak_x
        kernel_y += peak_y
    kernel_x *= normfactor/kernel_x.sum()
    kernel_y *= normfactor/kernel_y.sum()

    # Add in the offset copies of the main 1d kernels
    kernel_2d[cen + int(round(yshift*oversample))] = kernel_x
    kernel_2d[:, cen + int(round(xshift*oversample))] = kernel_y

    ### create and add in the more diffuse radial term
    # Model this as an expoential falloff outside the inner radius, times some scale factor relative to the above
    y, x = np.indices(kernel_2d.shape)
    r = np.sqrt((x-cen)**2 + (y-cen)**2) / oversample
    radial_term  = np.exp(-r/2/webbpsf.constants.MIRI_CRUCIFORM_INNER_RADIUS_PIX) * kernel_amp \
                   * (r > webbpsf.constants.MIRI_CRUCIFORM_INNER_RADIUS_PIX) \
                   * webbpsf.constants.MIRI_CRUCIFORM_RADIAL_SCALEFACTOR

    kernel_2d += radial_term

    return kernel_2d


def _apply_miri_scattering_kernel_2d(in_psf, kernel_2d, oversample):
    """
    Applies the detector scattering kernel created in _make_miri_scattering_kernel
    function to an input image. Code is adapted from
    MIRI-TN-00076-ATC_Imager_PSF_Issue_4.pdf

    Parameters
    ----------
    in_psf : ndarray
        PSF array upon which to apply the kernel
    kernel_x : ndarray
        The 1D kernel in the x direction, output from _make_miri_scattering_kernel.
        This will be transposed to create the kernel in the y direction.
    oversample : int
        Amount by which the input PSF is oversampled

    Returns
    -------
    im_conv_both : ndarray
        The input image convolved with the input kernel in both the x and
        y directions
    """

    # Convolve the input PSF with the kernel for scattering
    im_conv = astropy.convolution.convolve_fft(in_psf, kernel_2d, boundary='fill', fill_value=0.0,
                                                 normalize_kernel=False, nan_treatment='fill', allow_huge = True)


    # Normalize.
    # Note, it appears we do need to correct the amplitude for the sampling factor. Might as well do that here.
    im_conv_both = im_conv / oversample**2

    return im_conv_both


def get_miri_cruciform_amplitude(filt):
    # Default kernel amplitude values from modeling in MIRI-TN-00076-ATC_Imager_PSF_Issue_4.pdf
    kernel_amp_dict = {
        'F560W': 0.00220,
        'F770W': 0.00139,
        'F1000W': 0.00034,
        'F1130W': 0.00007,
        'F1280W': 0.00011,
        'F1500W': 0.0,
        'F1800W': 0.0,
        'F2100W': 0.0,
        'F2550W': 0.0,
        'FND': 0.00087,
        'F1065C': 0.00010,
        'F1140C': 0.00007,
        'F1550C': 0.0,
        'F2300C': 0.0,
    }

    # The above values are from that tech report, but empirically we need higher values to
    # better match the MIRI CDP PSFS. See e.g. MIRI_FM_MIRIMAGE_F560W_PSF_07.02.00.fits
    # and https://github.com/spacetelescope/webbpsf/issues/415
    kernel_amp_corrections = {
        'F560W': 4.05,
        'F770W': 4.1,
        'F1000W': 3.8,
        'F1130W': 2.5,
        'F1280W': 2.5,
        'F1065C': 2.5,
        'F1140C': 2.5,
        'FND': 3.0,
    }
    # FND value is a WAG, interpolating between the F1000W and F1130W values; in reality it varies over that
    # huge bandpass, but we can't compute it per-wavelength here.

    # In-flight correction based on measured cycle 1 ePSFs, coarsely
    for k in kernel_amp_corrections:
        kernel_amp_corrections[k] *= 0.5

    kernel_amp = kernel_amp_dict[filt]

    if filt in kernel_amp_corrections:
        kernel_amp *= kernel_amp_corrections[filt]
    return kernel_amp


def apply_miri_scattering(hdulist_or_filename=None, kernel_amp=None, old_method=False):
    """
    Apply a distortion caused by the MIRI scattering cross artifact effect.
    In short we convolve a 2D exponentially decaying cross to the PSF where
    the amplitude of the exponential function is determined by the filter of
    the PSF. A full description of the distortion and the original code can
    be found in MIRI-TN-00076-ATC_Imager_PSF_Issue_4.pdf

    Note, this code **edits in place Extension 1 of the supplied HDUlist**. In the typical case where the
    input PSF is calculated as Extension 0, the calling function must put a copy of that into Extension 1
    which this will then modify. This happens in webbpsf_core.py/JWInstrument._calc_psf_format_output,
    which is where this is called from in the usual course of operation.

    Parameters
    ----------
    hdulist_or_filename :
        A PSF from WebbPSF, either as an HDUlist object or as a filename
    kernel_amp: float
        Detector scattering kernel amplitude. If set to None,
        function will pull the value based on best fit analysis
        using the input PSF's filter. Default = None.

    Returns
    -------
    psf : HDUlist object
        PSF with MIRI detector scattering effect applied
    """

    # Read in input PSF
    if isinstance(hdulist_or_filename, str):
        hdu_list = fits.open(hdulist_or_filename)
    elif isinstance(hdulist_or_filename, fits.HDUList):
        hdu_list = hdulist_or_filename
    else:
        raise ValueError('input must be a filename or HDUlist')

    # Create a copy of the PSF
    psf = copy.deepcopy(hdu_list)

    # Log instrument name and filter
    instrument = hdu_list[0].header['INSTRUME'].upper()
    filt = hdu_list[0].header['FILTER'].upper()

    if instrument != 'MIRI':
        raise ValueError("MIRI's Scattering Effect should only be applied to MIRI PSFs")

    # Set values if not already set by a keyword argument
    if kernel_amp is None:
        kernel_amp = get_miri_cruciform_amplitude(filt)

    ext = 1  # edit the oversampled PSF (OVERDIST extension)

    # Set over-sample value
    oversample = psf[ext].header['DET_SAMP']

    # Read in PSF
    in_psf = psf[ext].data

    # create cruciform model using improved method using a 2d convolution kernel, attempting to model more physics.
    kernel_2d = _make_miri_scattering_kernel_2d(in_psf, kernel_amp, oversample,
        detector_position= (hdu_list[0].header['DET_X'], hdu_list[0].header['DET_Y']),
        wavelength = hdu_list[0].header['WAVELEN']*1e6 )
    im_conv_both = _apply_miri_scattering_kernel_2d(in_psf, kernel_2d, oversample)

    # Add this 2D scattered light output to the PSF
    psf_new = in_psf + im_conv_both

    # To ensure conservation of intensity, normalize the psf
    psf_new *= in_psf.sum() / psf_new.sum()

    # Apply data to correct extensions
    psf[ext].data = psf_new

    # Set new header keywords
    psf[ext].header['MIR_DIST'] = ('True', 'MIRI detector scattering applied')
    psf[ext].header['KERN_AMP'] = (kernel_amp, 'Amplitude (A) in kernel function A*exp(-x/B)')
    psf[ext].header['KERNFOLD'] = (25, 'e-folding length (B) in kernel func A*exp(-x/B)')

    return psf


def _show_miri_cruciform_kernel(filt, npix=101, oversample=4, detector_position=(512,512), ax=None):
    """ utility function for viewing/visualizing the cruciform kernel
    """
    import matplotlib

    placeholder = np.zeros((npix*oversample, npix*oversample))
    kernel_amp = get_miri_cruciform_amplitude(filt)
    extent =[-npix/2, npix/2, -npix/2, npix/2]

    kernel_2d = _make_miri_scattering_kernel_2d(placeholder, kernel_amp, oversample,
        detector_position= detector_position)
    norm = matplotlib.colors.LogNorm(1e-6, 1)
    cmap = matplotlib.cm.viridis
    cmap.set_bad(cmap(0))
    if ax is None:
        ax = matplotlib.pyplot.gca()
    ax.imshow(kernel_2d, norm=norm, cmap=cmap, extent=extent, origin='lower')
    ax.set_title(f"MIRI cruciform model for {filt}, position {detector_position}, oversample {oversample}")
    ax.plot(0,0,marker='+', color='yellow')

    matplotlib.pyplot.colorbar(mappable=ax.images[0])


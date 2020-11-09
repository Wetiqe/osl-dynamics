"""Functions to perform spectral analysis.

"""

import logging
from typing import Tuple

import numpy as np
from scipy.signal.windows import dpss
from sklearn.decomposition import non_negative_factorization
from tqdm import trange
from vrad.analysis.functions import fourier_transform, nextpow2, validate_array
from vrad.analysis.time_series import get_state_time_series
from vrad.data.manipulation import scale

_logger = logging.getLogger("VRAD")


def multitaper(
    data: np.ndarray,
    sampling_frequency: float,
    nfft: int = None,
    tapers: np.ndarray = None,
    time_half_bandwidth: float = None,
    n_tapers: int = None,
    args_range: list = None,
) -> np.ndarray:
    """Calculates a power (or cross) spectral density using the multitaper method.

    Parameters
    ----------
    data : np.ndarray
        Data with shape (n_samples, n_channels) to calculate a multitaper for.
    sampling_frequency : float
        Frequency used to sample the data (Hz).
    nfft : int
        Number of points in the FFT.
    tapers : np.ndarray
        Taper functions.
    time_half_bandwidth : float
        Parameter to control the resolution of the multitaper.
    n_tapers : int
        Number of tapers.
    args_range : list
        Minimum and maximum indices of the multitaper to keep.

    Returns
    -------
    np.ndarray
        Power (or cross) spectral density with shape (n_channels, n_channels, n_f).

    """
    # Transpose the data so that it is [n_channels, n_samples]
    data = np.transpose(data)

    # Number of channels and length of each signal
    n_channels, n_samples = data.shape

    # Number of FFT data points to calculate
    if nfft is None:
        nfft = max(256, 2 ** nextpow2(n_samples))

    # If tapers are not passed we generate them here
    if tapers is None:

        # Check the time half width bandwidth and number of tapers has been passed
        if time_half_bandwidth is None or n_tapers is None:
            raise ValueError("time_half_bandwidth and n_tapers must be passed.")

        # Calculate tapers
        tapers = dpss(n_samples, NW=time_half_bandwidth, Kmax=n_tapers)
        tapers *= np.sqrt(sampling_frequency)

    else:
        # Get number of tapers from the tapers passed
        n_tapers = len(tapers)

    # Multiply the data by the tapers
    data = data[np.newaxis, :, :] * tapers[:, np.newaxis, :]

    # Calculate the FFT, X, which has shape [n_tapers, n_channels, n_f]
    X = fourier_transform(data, sampling_frequency, nfft=nfft, args_range=args_range)

    # Number of frequency bins in the FFT
    n_f = X.shape[-1]

    # Calculate the periodogram with each taper
    P = np.zeros([n_channels, n_channels, n_f], dtype=np.complex_)
    for i in range(n_tapers):
        for j in range(n_channels):
            for k in range(j, n_channels):
                P[j, k] += np.conjugate(X[i, j]) * X[i, k]
                if i == n_tapers - 1 and k != j:
                    P[k, j] = np.conjugate(P[j, k])

    return P


def state_spectra(
    data: np.ndarray,
    state_mixing_factors: np.ndarray,
    sampling_frequency: float,
    time_half_bandwidth: float,
    n_tapers: int,
    segment_length: int = None,
    frequency_range: list = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculates spectra for inferred states.

    This includes power spectra and coherence.
    Follows the same procedure as the OSL function HMM-MAR/spectral/hmmspectamt.m

    Parameters
    ----------
    data : np.ndarray
        Raw time series data with shape (n_samples, n_channels).
    state_mixing_factors : np.ndarray
        Inferred state mixing factor alpha_t at each time point. Shape is (n_samples,
        n_states).
    sampling_frequency : float
        Frequency used to sample the data (Hz).
    time_half_bandwidth : float
        Parameter to control the resolution of the spectra.
    n_tapers : int
        Number of tapers to use when calculating the multitaper.
    segment_length : int
        Length of the data segement to use to calculate the multitaper.
    frequency_range : list
        Minimum and maximum frequency to keep.

    Returns
    -------
    frequencies : np.ndarray
        Frequencies of the power spectra and coherences. Shape is (n_f,).
    power_spectra : np.ndarray
        Power (or cross) spectra calculated for each state. Shape is (n_states,
        n_channels, n_channels, n_f).
    coherences : np.ndarray
        Coherences calculated for each state. Shape is (n_states, n_channels,
        n_channels, n_f).

    """
    # Validation
    if (isinstance(data, list) != isinstance(state_mixing_factors, list)) or (
        isinstance(data, np.ndarray) != isinstance(state_mixing_factors, np.ndarray)
    ):
        raise ValueError(
            f"data is type {type(data)} and state_mixing_factors is type "
            + f"{type(state_mixing_factors)}. They must both be lists or numpy arrays."
        )

    if isinstance(data, np.ndarray):
        if state_mixing_factors.shape[0] < data.shape[0]:
            # When we time embed we lose some data points so we trim the data
            n_padding = (data.shape[0] - state_mixing_factors.shape[0]) // 2
            data = data[n_padding:-n_padding]
        elif state_mixing_factors.shape[0] != data.shape[0]:
            raise ValueError("data cannot have less samples than state_mixing_factors.")

    if isinstance(data, list):
        # Check data and state mixing factors for the same number of subjects has
        # been passed
        if len(data) != len(state_mixing_factors):
            raise ValueError(
                "A different number of subjects has been passed for "
                + f"data and state_mixing_factors: len(data)={len(data)}, "
                + f"len(state_mixing_factors)={len(state_mixing_factors)}."
            )

        # Check the number of samples in data and state_mixing_factors
        for i in range(len(state_mixing_factors)):
            if state_mixing_factors[i].shape[0] < data[i].shape[0]:
                # When we time embed we lose some data points so we trim the data
                n_padding = (data[i].shape[0] - state_mixing_factors[i].shape[0]) // 2
                data = data[n_padding:-n_padding]
            elif state_mixing_factors[i].shape[0] != data[i].shape[0]:
                raise ValueError(
                    "data cannot have less samples than state_mixing_factors."
                )

        # Concatenate the data and state mixing factors for each subject
        data = np.concatenate(data, axis=0)
        state_mixing_factors = np.concatenate(state_mixing_factors, axis=0)

    if data.ndim != 2:
        raise ValueError(
            "data must have shape (n_samples, n_states) "
            + "or (n_subjects, n_samples, n_states)."
        )

    if state_mixing_factors.ndim != 2:
        raise ValueError(
            "state_mixing_factors must have shape (n_samples, n_states) "
            + "or (n_subjects, n_samples, n_states)."
        )

    if segment_length is None:
        segment_length = 2 * sampling_frequency

    elif segment_length != 2 * sampling_frequency:
        _logger.warning("segment_length is recommended to be 2*sampling_frequency.")

    if frequency_range is None:
        frequency_range = [0, sampling_frequency / 2]

    # Standardise (z-transform) the data
    data = scale(data, axis=0)

    # Use the state mixing factors to get a time series for each state
    state_time_series = get_state_time_series(data, state_mixing_factors)

    # Number of subjects, states, samples and channels
    n_states, n_samples, n_channels = state_time_series.shape

    # Number of FFT data points to calculate
    nfft = max(256, 2 ** nextpow2(segment_length))

    # Calculate the argments to keep for the given frequency range
    frequencies = np.arange(0, sampling_frequency / 2, sampling_frequency / nfft)
    f_min_arg = np.argwhere(frequencies > frequency_range[0])[0, 0]
    f_max_arg = np.argwhere(frequencies < frequency_range[1])[-1, 0]
    frequencies = frequencies[f_min_arg : f_max_arg + 1]
    args_range = [f_min_arg, f_max_arg + 1]

    # Number of frequency bins
    n_f = args_range[1] - args_range[0]

    # Calculate tapers so we can estimate spectra with the multitaper method
    tapers = dpss(segment_length, NW=time_half_bandwidth, Kmax=n_tapers)
    tapers *= np.sqrt(sampling_frequency)

    # We will calculate the spectrum for several non-overlapping segments
    # of the time series and return the average over these segments.

    # Number of segments in the time series
    n_segments = round(n_samples / segment_length)

    # Power spectra for each state
    power_spectra = np.zeros([n_states, n_channels, n_channels, n_f], dtype=np.complex_)

    print("Calculating power spectra")
    for i in range(n_states):
        for j in trange(n_segments, desc=f"State {i}", ncols=98):

            # Time series for state i and segment j
            time_series_segment = state_time_series[
                i, j * segment_length : (j + 1) * segment_length
            ]

            # If we're missing samples we pad with zeros either side of the data
            if time_series_segment.shape[0] != segment_length:
                n_zeros = segment_length - time_series_segment.shape[0]
                n_padding = n_zeros // 2
                time_series_segment = np.pad(time_series_segment, n_padding)[
                    :, n_padding:-n_padding
                ]

            # Calculate the power (and cross) spectrum using the multitaper method
            power_spectra[i] += multitaper(
                time_series_segment,
                sampling_frequency,
                nfft=nfft,
                tapers=tapers,
                args_range=args_range,
            )

    # Normalise the power spectra
    sum_factors = np.sum(state_mixing_factors ** 2, axis=0)[
        ..., np.newaxis, np.newaxis, np.newaxis
    ]
    power_spectra *= n_samples / (sum_factors * n_tapers * n_segments)

    # Coherences for each state
    coherences = np.empty([n_states, n_channels, n_channels, n_f])

    print("Calculating coherences")
    for i in range(n_states):
        for j in range(n_channels):
            for k in range(n_channels):
                coherences[i, j, k] = abs(
                    power_spectra[i, j, k]
                    / np.sqrt(power_spectra[i, j, j] * power_spectra[i, k, k])
                )

    return frequencies, np.squeeze(power_spectra), np.squeeze(coherences)


def decompose_spectra(
    coherences: np.ndarray,
    n_components: int,
    max_iter: int = 50000,
    random_state: int = None,
    verbose: int = 0,
) -> np.ndarray:
    """Performs spectral decomposition using coherences.

    Uses non-negative matrix factorization to decompose spectra.
    Follows the same procedure as the OSL funciton HMM-MAR/spectral/spectdecompose.m

    Parameters
    ----------
    coherences : np.ndarray
        Coherences with shape (n_states, n_channels, n_channels, n_f).
    n_components : int
        Number of spectral components to fit.
    max_iter : int
        Maximum number of iterations in sklearn's non_negative_factorization.
    random_state : int
        Seed for the random number generator.
    verbose : int
        Show verbose? (1) yes, (0) no.

    Returns
    -------
    components : np.ndarray
        Spectral components. Shape is (n_components, n_f).

    """
    print("Performing spectral decomposition")

    # Validation
    error_message = (
        "coherences must be a numpy array with shape (n_channels, n_channels), "
        + "(n_states, n_channels, n_channels) or (n_subjects, n_states, "
        + "n_channels, n_channels)."
    )
    coherences = validate_array(
        coherences,
        correct_dimensionality=5,
        allow_dimensions=[2, 3, 4],
        error_message=error_message,
    )

    # Number of subjects, states, channels and frequency bins
    n_subjects, n_states, n_channels, n_channels, n_f = coherences.shape

    # Indices of the upper triangle of the [n_channels, n_channels, n_f] sub-array
    i, j = np.triu_indices(n_channels, 1)

    # Concatenate coherences for each subject and state and only keep the upper triangle
    coherences = coherences[:, :, i, j].reshape(-1, n_f)

    # Perform non-negative matrix factorisation
    _, components, _ = non_negative_factorization(
        coherences,
        n_components=n_components,
        init=None,
        max_iter=max_iter,
        random_state=random_state,
        verbose=verbose,
    )

    # Order the weights and components in ascending frequency
    order = np.argsort(components.argmax(axis=1))
    components = components[order]

    return components
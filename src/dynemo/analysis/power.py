"""Functions to calculate and save power maps.

"""

import logging
from pathlib import Path

import nibabel as nib
import numpy as np
from nilearn import plotting
from tqdm import trange
from dynemo import array_ops, files

_logger = logging.getLogger("DyNeMo")


def variance_from_spectra(
    frequencies: np.ndarray,
    power_spectra: np.ndarray,
    components: np.ndarray = None,
    frequency_range: list = None,
) -> np.ndarray:
    """Calculates variance from power spectra.

    Parameters
    ----------
    frequencies : np.ndarray
        Frequency axis of the PSDs. Only used if frequency_range is given.
    power_spectra : np.ndarray
        Power/cross spectra for each channel. Shape is (n_modes, n_channels,
        n_channels, n_f).
    components : np.ndarray
        Spectral components. Shape is (n_components, n_f). Optional.
    frequency_range : list
        Frequency range to integrate the PSD over (Hz). Optional: default is full
        range.

    Returns
    -------
    np.ndarray
        Variance over a frequency band for each component of each mode.
        Shape is (n_components, n_modes, n_channels, n_channels).
    """

    # Validation
    error_message = (
        "A (n_channels, n_channels, n_frequency_bins), "
        + "(n_modes, n_channels, n_channels, n_frequency_bins) or "
        + "(n_subjects, n_modes, n_channels, n_channels, n_frequency_bins) "
        + "array must be passed."
    )
    power_spectra = array_ops.validate(
        power_spectra,
        correct_dimensionality=5,
        allow_dimensions=[3, 4],
        error_message=error_message,
    )

    if components is not None and frequency_range is not None:
        raise ValueError(
            "Only one of the arguments components or frequency range can be passed."
        )

    if frequency_range is not None and frequencies is None:
        raise ValueError(
            "If frequency_range is passed, frequenices must also be passed."
        )

    # Dimensions
    n_subjects, n_modes, n_channels, n_channels, n_f = power_spectra.shape
    if components is None:
        n_components = 1
    else:
        n_components = components.shape[0]

    # Calculate power maps for each subject
    var = []
    for i in range(n_subjects):

        # Remove cross-spectral densities from the power spectra array
        # and concatenate over modes
        psd = power_spectra[i, :, range(n_channels), range(n_channels)]
        psd = np.swapaxes(psd, 0, 1)
        psd = psd.reshape(-1, n_f)
        psd = psd.real

        if components is not None:
            # Calculate PSD for each spectral component
            p = components @ psd.T

        else:
            # Integrate over the given frequency range
            if frequency_range is None:
                p = np.sum(psd, axis=-1)
            else:
                f_min_arg = np.argwhere(frequencies > frequency_range[0])[0, 0]
                f_max_arg = np.argwhere(frequencies < frequency_range[1])[-1, 0]
                if f_max_arg < f_min_arg:
                    raise ValueError("Cannot select the specified frequency range.")
                p = np.sum(psd[..., f_min_arg : f_max_arg + 1], axis=-1)

        p = p.reshape(n_components, n_modes, n_channels)

        # Variance
        v = np.zeros([n_components, n_modes, n_channels, n_channels])
        v[:, :, range(n_channels), range(n_channels)] = p
        var.append(v)

    return np.squeeze(var)


def power_map_grid(
    mask_file: str,
    parcellation_file: str,
    power_map: np.ndarray,
    component: int = 0,
    subtract_mean: bool = False,
    mean_weights: np.ndarray = None,
) -> np.ndarray:
    """Takes a power map and returns the power at locations on a spatial grid."""

    # Validation
    error_message = (
        f"Dimensionality of power_map must be 2, 3, or 4, got ndim={power_map.ndim}."
    )
    power_map = array_ops.validate(
        power_map,
        correct_dimensionality=4,
        allow_dimensions=[2, 3],
        error_message=error_message,
    )

    # Load the mask
    mask = nib.load(mask_file)
    mask_grid = mask.get_data()
    mask_grid = mask_grid.ravel(order="F")

    # Get indices of non-zero elements, i.e. those which contain the brain
    non_zero_voxels = mask_grid != 0

    # Load the parcellation
    parcellation = nib.load(parcellation_file)
    parcellation_grid = parcellation.get_data()

    # Make a 2D array of voxel weights for each parcel
    n_parcels = parcellation.shape[-1]
    voxel_weights = parcellation_grid.reshape(-1, n_parcels, order="F")[non_zero_voxels]

    # Normalise the voxels weights
    voxel_weights /= voxel_weights.max(axis=0)[np.newaxis, ...]

    # Generate a spatial map vector for each mode
    n_voxels = voxel_weights.shape[0]
    n_modes = power_map.shape[1]
    spatial_map_values = np.empty([n_voxels, n_modes])
    for i in range(n_modes):
        spatial_map_values[:, i] = voxel_weights @ np.diag(
            np.squeeze(power_map[component, i])
        )

    # Subtract weighted mean
    if subtract_mean:
        spatial_map_values -= np.average(
            spatial_map_values,
            axis=1,
            weights=mean_weights,
        )[..., np.newaxis]

    # Final spatial map as a 3D grid for each mode
    spatial_map = np.zeros([mask_grid.shape[0], n_modes])
    spatial_map[non_zero_voxels] = spatial_map_values
    spatial_map = spatial_map.reshape(
        mask.shape[0], mask.shape[1], mask.shape[2], n_modes, order="F"
    )

    return spatial_map


def save(
    power_map: np.ndarray,
    filename: str,
    mask_file: str,
    parcellation_file: str,
    component: int = 0,
    subtract_mean: bool = False,
    mean_weights: np.ndarray = None,
    **plot_kwargs,
):
    """Saves power maps.

    Parameters
    ----------
    power_map : np.ndarray
        Power map to save.
        Shape must be (n_components, n_modes, n_channels, n_channels).
    filename : str
        Output filename. If extension is .nii.gz the power map is saved as a
        NIFTI file. Or if the extension is png, it is saved as images.
    mask_file : str
        Mask file used to preprocess the training data.
    parcellation_file : str
        Parcellation file used to parcelate the training data.
    component : int
        Spectral component to save. Optional.
    subtract_mean : bool
        Should we subtract the mean power across modes? Optional: default is False.
    mean_weights: np.ndarray
        Numpy array with weightings for each mode to use to calculate the mean.
        Optional, default is equal weighting.
    """
    # Validation
    if ".nii.gz" not in filename and ".png" not in filename:
        raise ValueError("filename must have extension .nii.gz or .png.")
    mask_file = files.check_exists(mask_file, files.mask.directory)
    parcellation_file = files.check_exists(
        parcellation_file, files.parcellation.directory
    )

    # Calculate power maps
    power_map = power_map_grid(
        mask_file=mask_file,
        parcellation_file=parcellation_file,
        power_map=power_map,
        component=component,
        subtract_mean=subtract_mean,
        mean_weights=mean_weights,
    )

    # Load the mask
    mask = nib.load(mask_file)

    # Save as nii file
    if ".nii.gz" in filename:
        print(f"Saving {filename}")
        nii = nib.Nifti1Image(power_map, mask.affine, mask.header)
        nib.save(nii, filename)

    # Save each map as an image
    elif ".png" in filename:
        n_modes = power_map.shape[-1]
        for i in trange(n_modes, desc="Saving images", ncols=98):
            nii = nib.Nifti1Image(power_map[:, :, :, i], mask.affine, mask.header)
            output_file = "{fn.parent}/{fn.stem}{i:0{w}d}{fn.suffix}".format(
                fn=Path(filename), i=i, w=len(str(n_modes))
            )
            plotting.plot_img_on_surf(
                nii,
                views=["lateral", "medial"],
                hemispheres=["left", "right"],
                colorbar=True,
                output_file=output_file,
                **plot_kwargs,
            )
"""
SEM image quantification — pure functions, no I/O. Ported from the
original sem.py verbatim (same algorithms/thresholds), so results match
whatever the notebook prototypes already produced.
"""
from __future__ import annotations

import numpy as np
import cv2
from skimage import morphology, measure
from scipy import ndimage


def particle_size_distribution(gray, um_per_pixel: float, min_area: int = 20) -> np.ndarray:
    """Otsu-threshold + connected-component sizing; diameters in µm, assuming circular particles."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_OTSU)
    labels = measure.label(th)
    props = measure.regionprops(labels)

    diameters = []
    for p in props:
        if p.area > min_area:
            d = 2 * np.sqrt(p.area / np.pi)
            diameters.append(d * um_per_pixel)
    return np.array(diameters)


def porosity_percent(gray, method: str = "otsu") -> float:
    if method == "otsu":
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_OTSU)
    else:
        thresh_val = np.percentile(gray, 10)
        th = (gray < thresh_val).astype(np.uint8) * 255
    pores = th == 0
    return float(100 * np.sum(pores) / pores.size)


def crack_metrics(gray, um_per_pixel: float, canny_low: int = 30, canny_high: int = 100):
    """Canny edges + skeletonization -> (total crack length µm, crack length per µm²)."""
    edges = cv2.Canny(gray, canny_low, canny_high)
    skel = morphology.skeletonize(edges > 0)
    crack_length = float(np.sum(skel) * um_per_pixel)
    area_um2 = gray.shape[0] * gray.shape[1] * um_per_pixel * um_per_pixel
    crack_density = crack_length / area_um2 if area_um2 else 0.0
    return crack_length, crack_density


def contact_area_fraction(binary_gaps) -> float:
    contact = ~binary_gaps
    return float(100 * np.sum(contact) / contact.size)


def gap_thickness_distribution(binary_gaps, um_per_pixel: float) -> np.ndarray:
    dist = ndimage.distance_transform_edt(binary_gaps)
    return dist[dist > 0] * um_per_pixel


def morphology_uniformity(gray) -> float:
    return float(np.std(gray))


def binder_distribution(gray) -> float:
    return float(np.var(gray))


def interface_quality_score(gray, binary_gaps, um_per_pixel: float, is_delaminated: bool = False) -> float:
    gap_pct = 100 * np.sum(binary_gaps) / binary_gaps.size
    if is_delaminated:
        return float(max(0, 100 - (gap_pct * 8)))
    return float(max(80, 100 - gap_pct))


def detect_gaps(gray, percentile_threshold: float = 5, min_size: int = 100, is_delaminated: bool = True):
    blur = cv2.GaussianBlur(gray, (7, 7), 0)
    gap_threshold = np.percentile(blur, percentile_threshold)
    binary_gaps = blur < gap_threshold
    if is_delaminated:
        binary_gaps = morphology.remove_small_objects(binary_gaps, max_size=min_size)
    else:
        binary_gaps = morphology.remove_small_objects(binary_gaps, max_size=min_size * 2)
        binary_gaps = morphology.erosion(binary_gaps, morphology.disk(2))
    return binary_gaps


def sem_full_quantification(gray, um_per_pixel: float, binary_gaps=None,
                             detect_gaps_params=None, verbose: bool = False) -> dict:
    """Run every metric above and return one flat results dict (plus the
    raw arrays needed for histogram plots: 'binary_gaps', 'gap_distribution',
    'particle_diameters')."""
    if binary_gaps is None:
        params = detect_gaps_params or {"percentile_threshold": 5, "is_delaminated": True}
        binary_gaps = detect_gaps(gray, **params)

    diam = particle_size_distribution(gray, um_per_pixel)
    por = porosity_percent(gray)
    crack_len, crack_den = crack_metrics(gray, um_per_pixel)
    contact = contact_area_fraction(binary_gaps)
    gap_dist = gap_thickness_distribution(binary_gaps, um_per_pixel)
    uniform = morphology_uniformity(gray)
    binder = binder_distribution(gray)
    quality = interface_quality_score(gray, binary_gaps, um_per_pixel)

    if verbose:
        print(f"  particles: {len(diam)}  porosity: {por:.2f}%  contact: {contact:.2f}%  quality: {quality:.1f}")

    return {
        "mean_particle_um": float(np.mean(diam)) if len(diam) > 0 else 0.0,
        "median_particle_um": float(np.median(diam)) if len(diam) > 0 else 0.0,
        "std_particle_um": float(np.std(diam)) if len(diam) > 0 else 0.0,
        "porosity_pct": por,
        "crack_length_um": crack_len,
        "crack_density": crack_den,
        "contact_area_pct": contact,
        "mean_gap_um": float(np.mean(gap_dist)) if len(gap_dist) > 0 else 0.0,
        "max_gap_um": float(np.max(gap_dist)) if len(gap_dist) > 0 else 0.0,
        "uniformity_std": uniform,
        "binder_var": binder,
        "quality_score": quality,
        "binary_gaps": binary_gaps,
        "gap_distribution": gap_dist,
        "particle_diameters": diam,
    }

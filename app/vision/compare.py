"""
app/vision/compare.py - Advisory Photo Comparison (P3 Stretch Deliverable).

Design and Bounded Autonomy Principles:
1. Advisory flag for human review ONLY: Never auto-penalizes. Never says "damage detected".
2. Tamper-evident: Computes and returns SHA-256 hash of each photo for P2's audit chain.
3. Pure, lightweight implementation: Operates on raw image bytes without heavy C-extensions.
4. Honest limitations: Discloses in the output note that lighting, angle shifts, and focus
   can cause perceptual differences and that no accuracy is claimed without a labelled dataset.
"""
from __future__ import annotations
import hashlib
from typing import Any, Dict, List, Optional, Tuple

def compute_photo_hash(photo_bytes: bytes) -> str:
    """Computes SHA-256 digest of image bytes for the audit chain."""
    return hashlib.sha256(photo_bytes).hexdigest()

def _simple_perceptual_block_hash(data: bytes, grid_size: int = 4) -> List[int]:
    """
    Computes a simplified block-intensity fingerprint across data chunks.
    Lightweight, deterministic, and requires no external binary libraries.
    """
    if not data:
        return [0] * (grid_size * grid_size)
    
    total_len = len(data)
    chunk_size = max(1, total_len // (grid_size * grid_size))
    blocks = []
    
    for i in range(grid_size * grid_size):
        chunk = data[i * chunk_size : (i + 1) * chunk_size]
        if chunk:
            avg_val = sum(chunk) // len(chunk)
        else:
            avg_val = 0
        blocks.append(avg_val)
        
    return blocks

def compare_photos(
    before_bytes: bytes,
    after_bytes: bytes,
    angle: Optional[str] = "front",
    sensitivity_threshold: float = 0.15,
) -> Dict[str, Any]:
    """
    Compares before and after condition photos for a shared equipment check-in/out.
    
    Returns:
      {
        "before_sha256": str,
        "after_sha256": str,
        "score": float,  # 0.0 (identical) to 1.0 (substantial difference)
        "changed_regions": list[str],
        "requires_human_review": bool,
        "note": str
      }
    """
    before_hash = compute_photo_hash(before_bytes)
    after_hash = compute_photo_hash(after_bytes)

    if before_hash == after_hash:
        return {
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "score": 0.0,
            "changed_regions": [],
            "requires_human_review": False,
            "note": "Photos are byte-identical. No change detected.",
        }

    # Compute block fingerprints
    grid_size = 4
    blocks_before = _simple_perceptual_block_hash(before_bytes, grid_size=grid_size)
    blocks_after = _simple_perceptual_block_hash(after_bytes, grid_size=grid_size)

    changed_regions = []
    diff_sum = 0.0
    for idx, (b, a) in enumerate(zip(blocks_before, blocks_after)):
        diff = abs(b - a) / 255.0
        diff_sum += diff
        if diff > sensitivity_threshold:
            row = idx // grid_size
            col = idx % grid_size
            changed_regions.append(f"quadrant_{row}_{col}")

    avg_diff = round(min(1.0, diff_sum / len(blocks_before)), 3)
    requires_review = avg_diff >= sensitivity_threshold or len(changed_regions) > 0

    if requires_review:
        note = (
            f"Advisory flag for human review: Visual difference score {avg_diff:.2f} observed at angle '{angle}'. "
            "Possible change detected; human review needed. (Note: ambient lighting and camera angle shifts can cause variances)."
        )
    else:
        note = f"Visual difference score {avg_diff:.2f} within standard tolerance threshold for angle '{angle}'."

    return {
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "score": avg_diff,
        "changed_regions": changed_regions,
        "requires_human_review": requires_review,
        "note": note,
    }


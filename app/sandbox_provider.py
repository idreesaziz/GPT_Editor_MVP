import logging
import os
import subprocess
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def _create_dummy_video(filename: str, metadata: Dict[str, Any], sandbox_path: str):
    # This function is now simpler as `filename` is always a simple name
    try:
        width = metadata.get('width', 640)
        height = metadata.get('height', 480)
        duration = metadata.get('duration', 5)
        rate = metadata.get('frame_rate', 15)
        output_path = os.path.join(sandbox_path, filename)
        command = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', f'color=c=black:s={width}x{height}:r={rate}:d={duration}',
            '-f', 'lavfi', '-i', f'anullsrc=r=44100',
            '-t', str(duration),
            output_path
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        logger.debug(f"Created dummy video: {output_path} with metadata {metadata}")
    except Exception as e:
        logger.error(f"Failed to create dummy video {filename}: {e}")
        open(os.path.join(sandbox_path, filename), 'a').close()

def _create_dummy_generic(filename: str, metadata: Dict[str, Any], sandbox_path: str):
    output_path = os.path.join(sandbox_path, filename)
    open(output_path, 'a').close()
    logger.debug(f"Created generic empty dummy file: {output_path}")

DUMMY_GENERATORS = { "video": _create_dummy_video, "generic_file": _create_dummy_generic, "unknown": _create_dummy_generic }

def populate_sandbox(sandbox_path: str, asset_logs: List[Dict[str, Any]]):
    """
    Populates a given sandbox directory with high-fidelity dummy assets.
    """
    logger.debug(f"Populating sandbox at {sandbox_path} with {len(asset_logs)} asset(s).")
    for asset in asset_logs:
        filename = asset["filename"]
        asset_type = asset.get("type", "unknown")
        generator_func = DUMMY_GENERATORS.get(asset_type)
        
        if generator_func:
            generator_func(filename, asset.get("metadata", {}), sandbox_path)
        else:
            _create_dummy_generic(filename, {}, sandbox_path)
import subprocess
import json
import logging
import os

logger = logging.getLogger(__name__)

def get_asset_metadata(file_path: str) -> dict:
    """
    Gets metadata for a given asset. For now, focuses on video.
    Returns a dictionary with asset type and specific metadata.
    """
    if not os.path.exists(file_path):
        logger.warning(f"Metadata requested for a non-existent file: {file_path}")
        return {"type": "unknown", "error": "File not found"}

    file_extension = os.path.splitext(file_path)[1].lower()

    if file_extension in ['.mp4', '.mov', '.mkv', '.avi']:
        try:
            command = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                file_path
            ]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            data = json.loads(result.stdout)
            
            video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                raise ValueError("No video stream found")

            # Extract key properties
            metadata = {
                'width': int(video_stream['width']),
                'height': int(video_stream['height']),
                'duration': float(data['format'].get('duration', video_stream.get('duration', 0))),
                'frame_rate': eval(video_stream.get('r_frame_rate', '0/1')),
            }
            return {"type": "video", "metadata": metadata}

        except (subprocess.CalledProcessError, ValueError, KeyError) as e:
            logger.error(f"Failed to get video metadata from {file_path}: {e}")
            return {"type": "video", "error": str(e)}
    
    # Future: Add handlers for other types like 'image', 'audio', 'text'
    else:
        logger.info(f"Unsupported asset type '{file_extension}' for metadata extraction. Treating as generic file.")
        return {"type": "generic_file", "metadata": {"size": os.path.getsize(file_path)}}
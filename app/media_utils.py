import subprocess
import json
import logging
import os

logger = logging.getLogger(__name__)

def get_asset_metadata(file_path: str) -> dict:
    """
    Gets metadata for a given asset. Supports video, image, and audio files.
    Returns a dictionary with asset type and specific metadata.
    """
    if not os.path.exists(file_path):
        logger.warning(f"Metadata requested for a non-existent file: {file_path}")
        return {"type": "unknown", "error": "File not found"}

    file_extension = os.path.splitext(file_path)[1].lower()

    # Video formats
    if file_extension in ['.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.wmv', '.m4v']:
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
            audio_stream = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
            
            if not video_stream:
                raise ValueError("No video stream found")

            # Extract key properties
            metadata = {
                'width': int(video_stream['width']),
                'height': int(video_stream['height']),
                'duration': float(data['format'].get('duration', video_stream.get('duration', 0))),
                'frame_rate': eval(video_stream.get('r_frame_rate', '0/1')),
                'has_audio': audio_stream is not None,
                'codec': video_stream.get('codec_name', 'unknown')
            }
            return {"type": "video", "metadata": metadata}

        except (subprocess.CalledProcessError, ValueError, KeyError) as e:
            logger.error(f"Failed to get video metadata from {file_path}: {e}")
            return {"type": "video", "error": str(e)}
    
    # Image formats
    elif file_extension in ['.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tga', '.webp', '.svg']:
        try:
            command = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                file_path
            ]
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            data = json.loads(result.stdout)
            
            # For images, look for any stream that has width/height
            image_stream = next((s for s in data['streams'] if 'width' in s and 'height' in s), None)
            
            if not image_stream:
                raise ValueError("No image dimensions found")

            metadata = {
                'width': int(image_stream['width']),
                'height': int(image_stream['height']),
                'codec': image_stream.get('codec_name', 'unknown'),
                'format': file_extension.lstrip('.')
            }
            return {"type": "image", "metadata": metadata}

        except (subprocess.CalledProcessError, ValueError, KeyError) as e:
            logger.error(f"Failed to get image metadata from {file_path}: {e}")
            return {"type": "image", "error": str(e)}
    
    # Audio formats
    elif file_extension in ['.mp3', '.wav', '.aac', '.flac', '.ogg', '.m4a', '.wma', '.opus']:
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
            
            audio_stream = next((s for s in data['streams'] if s['codec_type'] == 'audio'), None)
            
            if not audio_stream:
                raise ValueError("No audio stream found")

            metadata = {
                'duration': float(data['format'].get('duration', audio_stream.get('duration', 0))),
                'sample_rate': int(audio_stream.get('sample_rate', 0)),
                'channels': int(audio_stream.get('channels', 0)),
                'codec': audio_stream.get('codec_name', 'unknown'),
                'bitrate': int(data['format'].get('bit_rate', 0))
            }
            return {"type": "audio", "metadata": metadata}

        except (subprocess.CalledProcessError, ValueError, KeyError) as e:
            logger.error(f"Failed to get audio metadata from {file_path}: {e}")
            return {"type": "audio", "error": str(e)}
    
    # Unsupported file types
    else:
        logger.info(f"Unsupported asset type '{file_extension}' for metadata extraction. Treating as generic file.")
        return {"type": "generic_file", "metadata": {"size": os.path.getsize(file_path)}}
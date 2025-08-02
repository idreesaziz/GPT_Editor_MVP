#!/usr/bin/env python3

import sys
import os
import tempfile
import logging

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from plugins.ffmpeg_plugin import FFmpegProcessor

def test_ffmpeg_production_scenario():
    """Test the FFmpeg plugin with production-like scenario"""
    
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    logger = logging.getLogger('test')
    
    # Simulate production environment paths
    session_path = "/home/idrees-mustafa/Dev/editor-MVP/GPT_Editor_MVP/sessions/ba45522c-5c3f-4835-b7ce-fd52755f3706"
    input_file_rel = "assets/sunset_image/image.png"  # Relative path as passed by orchestrator
    asset_unit_path = os.path.join(session_path, "assets", "black_and_white_sunset_image")
    
    # Create the asset unit directory if it doesn't exist
    os.makedirs(asset_unit_path, exist_ok=True)
    
    # Check if input file exists
    full_input_path = os.path.join(session_path, input_file_rel)
    if not os.path.exists(full_input_path):
        logger.error(f"Input file not found: {full_input_path}")
        return False
    
    logger.info(f"Testing with session path: {session_path}")
    logger.info(f"Input file (relative): {input_file_rel}")
    logger.info(f"Asset unit path: {asset_unit_path}")
    logger.info(f"Full input path: {full_input_path}")
    
    # Test task details
    task_details = {
        "unit_id": "black_and_white_sunset_image",
        "task": "Convert the input image to black and white (grayscale). Preserve the original resolution and quality.",
        "output_filename": "image.png",
        "input_file": input_file_rel  # This is what orchestrator passes
    }
    
    try:
        # Initialize FFmpeg plugin
        plugin = FFmpegProcessor()
        logger.info("FFmpeg plugin initialized successfully")
        
        # Execute the task
        result = plugin.execute_task(task_details, asset_unit_path, logger)
        
        if result:
            output_path = os.path.join(asset_unit_path, result[0])
            if os.path.exists(output_path):
                logger.info(f"SUCCESS: Output file created at {output_path}")
                file_size = os.path.getsize(output_path)
                logger.info(f"Output file size: {file_size} bytes")
                return True
            else:
                logger.error("Output file was not created")
                return False
        else:
            logger.error("Plugin returned no result")
            return False
            
    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    success = test_ffmpeg_production_scenario()
    if success:
        print("\n✅ FFmpeg plugin production test PASSED")
        sys.exit(0)
    else:
        print("\n❌ FFmpeg plugin production test FAILED")
        sys.exit(1)

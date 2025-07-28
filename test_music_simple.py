#!/usr/bin/env python3

import os
import sys
import logging

# Add the current directory to Python path
sys.path.insert(0, '.')

# Set dummy mode
os.environ['MUSIC_DUMMY_MODE'] = 'true'

from app.plugins.music_plugin import MusicGenerator

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

try:
    # Create test task
    plugin = MusicGenerator()
    task_details = {
        'task': 'A calm, peaceful piano melody',
        'unit_id': 'test_music',
        'output_filename': 'test_music.wav'
    }

    # Create test directory
    test_path = 'test_music_output'
    os.makedirs(test_path, exist_ok=True)

    result = plugin.execute_task(task_details, test_path, logger)
    print(f'Success: {result}')
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()

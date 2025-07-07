import os
import subprocess
import sys
import ast
import logging
import json
import tempfile
from typing import Tuple, Optional

from .base import ToolPlugin

logger = logging.getLogger(__name__)

# Re-using _create_dummy_video, _get_video_metadata, _is_video_readable
# from ffmpeg_plugin for MoviePy validation, as MoviePy also works with video files.
# For a real project, these might be moved to a shared `media_utils.py` or similar.
def _create_dummy_video(output_path: str, metadata: dict):
    """Creates a dummy test pattern video with specific metadata."""
    try:
        width = metadata.get('width', 640)
        height = metadata.get('height', 480)
        duration = metadata.get('duration', 5)
        frame_rate = metadata.get('frame_rate', 24)
        
        command = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', f'testsrc=size={width}x{height}:rate={frame_rate}:duration={duration}',
            '-f', 'lavfi', '-i', f'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-c:v', 'libx264', '-t', str(duration), '-pix_fmt', 'yuv420p',
            output_path
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create dummy video {output_path}: {e.stderr}")
        raise

def _get_video_metadata(file_path: str) -> Optional[dict]:
    """Gets metadata for a video file using ffprobe."""
    try:
        command = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', file_path
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
        if not video_stream: return None

        return {
            'width': int(video_stream['width']),
            'height': int(video_stream['height']),
            'duration': float(data['format'].get('duration', video_stream.get('duration', 0))),
            'frame_rate': eval(video_stream.get('r_frame_rate', '0/1')),
        }
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError, StopIteration) as e:
        logger.warning(f"Could not get metadata for {file_path}: {e}")
        return None

def _is_video_readable(file_path: str) -> bool:
    """Checks if a video file is readable by ffprobe without errors."""
    command = ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', file_path]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False

MOVIEPY_TIMEOUT = 45

class MoviePyPlugin(ToolPlugin):
    """Plugin for video editing using MoviePy."""

    @property
    def name(self) -> str:
        return "MoviePy Video Editor"

    @property
    def description(self) -> str:
        return (
            "A Python library for video editing, ideal for high-level operations like concatenation, "
            "clipping, resizing, adding text overlays, and simple effects directly in Python."
        )

    @property
    def prerequisites(self) -> str:
        return (
            "For operations requiring precise timing or dimensions, it is recommended to be "
            "preceded by a step using the 'Metadata Extractor' tool to get video properties."
        )

    def get_system_instruction(self) -> str:
        """Provides the specific system prompt for generating MoviePy scripts."""
        return """
    You are an AI assistant that generates Python scripts for video editing using the MoviePy library.
    The script will be given a dictionary of input files and a dictionary of output files.
    You must parse these dictionaries to get the filenames for your script.
    The script must only contain Python code using the 'moviepy' module.
    Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.

    **CRITICAL SCRIPTING RULES:**
    1.  **NO FUNCTIONS:** Your entire output must be top-level, executable Python code. Do NOT define any functions (e.g., `def main():`). The generated code will be executed as a flat script.
    2.  **Error Handling**: Do NOT use `sys.exit()`. Catch MoviePy-related exceptions (e.g., `OSError`, `IOError`) or general `Exception`, then raise a `RuntimeError` with a descriptive message to be handled by the calling code.
    3.  **Inputs and Outputs**: Determine the input video(s) and any other necessary files (like metadata) from the `inputs` dictionary provided in the user prompt context. Write your final video to the path specified in the `outputs` dictionary.
    4.  **Metadata**: If the `inputs` dictionary contains a path to a `.json` file, you MUST open and read this file to get video metadata (like width, height, duration) to construct more precise MoviePy operations.
    5.  **Always `clip.write_videofile`**: Ensure the final video clip is written to the specified output path using `clip.write_videofile(...)`. Use `codec="libx264"`, `audio_codec="aac"`, `fps=24` (or derived from metadata). Set `temp_audiofile` and `temp_videofile` to paths within `tempfile.gettempdir()` for robust temporary file handling. Set `remove_temp=True`.
    6.  **`ColorClip` FPS**: When creating a `ColorClip`, set its FPS using `.with_fps(FPS)` (e.g., `ColorClip(...).with_fps(FPS)`). Do NOT use `.set_fps()` as it is deprecated for `ColorClip`.
    7.  **`TextClip` Content**: Always use the `txt=` keyword argument to provide the text content to the `TextClip` constructor for clarity and robustness (e.g., `TextClip(txt="Your Text")`).
    8.  **MoviePy FX**: Use `import moviepy.video.fx as vfx` to access effects like `resize`, `fadein`, etc. Do NOT use `from moviepy.video.fx.all import vfx`.
    9.  **Avoid `print()`**: Do not include `print()` statements in the generated script. If debugging or logging is needed, use standard Python logging or raise exceptions.

    ---
    **IMPORTANT: Use the following modern import structure for MoviePy (version 2.0+). YOU MUST INCLUDE ALL OF THESE NECESSARY IMPORTS AT THE TOP OF THE SCRIPT, AS SHOWN HERE:**
    ```python
    import os
    import json
    import tempfile
    import math
    import numpy as np

    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.VideoClip import TextClip, ColorClip, VideoClip, AudioClip
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
    from moviepy.audio.AudioClip import AudioFileClip
    from moviepy.video.compositing.CompositeAudioClip import CompositeAudioClip
    from moviepy.editor import concatenate_videoclips, concatenate_audioclips, clips_array # These high-level functions are still in moviepy.editor
    import moviepy.video.fx as vfx  # For effects like resize, fadein, etc.
    import moviepy.audio.fx as afx  # For audio effects like AudioFadeIn, AudioFadeOut
    from PIL import Image, ImageDraw # Useful for custom frame generation
    ```
    **Note**: While `from moviepy import *` is shown in some examples for brevity, for better clarity and to avoid unintended imports, explicitly import the classes and functions you need as demonstrated above.

    **Sample patterns for I/O and metadata handling:**
    ```python
    output_path = outputs.get('final_video')
    if not output_path: raise RuntimeError("Output path 'final_video' not found in outputs dictionary.")

    input_video_path = inputs.get('initial_video') or inputs.get('previous_step_output')
    if not input_video_path: raise RuntimeError("Input video not found in inputs dictionary.")

    # Optional metadata loading if available
    metadata = {}
    if inputs.get('metadata_json'):
        try:
            with open(inputs['metadata_json'], 'r') as f:
                metadata = json.load(f).get('metadata', {})
        except Exception as e:
            raise RuntimeError(f"Could not load metadata from {inputs['metadata_json']}: {e}")

    # Clip variables for clean MoviePy operations
    clip = None
    final_clip = None

    try:
        # Load input video if path exists
        if os.path.exists(input_video_path):
            clip = VideoFileClip(input_video_path)
            # You might use clip.w, clip.h, clip.fps, clip.duration derived from loaded metadata or actual clip properties.
            # Example: Trim
            final_clip = clip.subclip(0, 5) # Example operation

        # If no input video, create a new video from scratch (e.g., ColorClip, TextClip)
        else:
            # Placeholder values if no metadata or specific input
            WIDTH = metadata.get('width', 1280)
            HEIGHT = metadata.get('height', 720)
            DURATION = metadata.get('duration', 5)
            FPS = metadata.get('frame_rate', 24)

            bg_color = (0, 128, 255)
            background = ColorClip(size=(WIDTH, HEIGHT), color=bg_color, duration=DURATION).with_fps(FPS)

            text_clip = TextClip(
                txt="Hello MoviePy.", # Use txt= for text content
                fontsize=72,
                font="Impact",
                color="white"
            ).set_duration(DURATION).set_position("center")

            final_clip = CompositeVideoClip([background, text_clip], size=(WIDTH, HEIGHT))

        # Always use tempfile.gettempdir() for temporary files
        pid = os.getpid()
        temp_audio_file = os.path.join(tempfile.gettempdir(), f"moviepy_temp_audio_{pid}.mp3")
        temp_video_file = os.path.join(tempfile.gettempdir(), f"moviepy_temp_video_{pid}.mp4")

        target_fps = final_clip.fps if final_clip and final_clip.fps else 24

        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            fps=target_fps,
            preset="medium",
            temp_audiofile=temp_audio_file,
            temp_videofile=temp_video_file,
            remove_temp=True
        )

    except Exception as e:
        # Ensure clips are closed in case of error
        if clip is not None: clip.close()
        if final_clip is not None: final_clip.close()
        raise RuntimeError(f"MoviePy script failed: {e}")

    finally:
        # Ensure clips are closed even if no error
        if clip is not None: clip.close()
        if final_clip is not None: final_clip.close()
    ```

    ---
    **I. Introduction to MoviePy: Core Concepts & Setup**

    MoviePy is a Python library for video editing, designed to be simple and flexible. It allows you to load, modify, composite, and save various types of video and audio resources, referred to as "clips."

    **What are Clips?**

    The first step for making a video with MoviePy is to load the resources you wish to include in the final video.

    There’s a lot of different resources you can use with MoviePy, and you will load different resources with different subtypes of Clip, and more precisely of AudioClip for any audio element, or VideoClip for any visual element.

    **Releasing resources by closing a clip**

    When you create some types of clip instances - e.g. VideoFileClip or AudioFileClip - MoviePy creates a subprocess and locks the file. In order to release these resources when you are finished you should call the close() method.

    This is more important for more complex applications and is particularly important when running on Windows. While Python’s garbage collector should eventually clean up the resources for you, closing them makes them available earlier.

    However, if you close a clip too early, methods on the clip (and any clips derived from it) become unsafe.

    So, the rules of thumb are:
    - Call `close()` on any clip that you construct once you have finished using it and have also finished using any clip that was derived from it.
    - Even if you close a `CompositeVideoClip` instance, you still need to close the clips it was created from.
    - Otherwise, if you have a clip that was created by deriving it from from another clip (e.g. by calling `with_mask()`), then generally you shouldn’t close it. Closing the original clip will also close the copy.

    Clips act as context managers. This means you can use them with a `with` statement, and they will automatically be closed at the end of the block, even if there is an exception.
    ```python
    # clip.close() is implicitly called, so the lock on my_audiofile.mp3 file
    # is immediately released.
    try:
        with AudioFileClip("example.wav") as clip:
            raise Exception("Let's simulate an exception")
    except Exception as e:
        pass # Use logging in production, print here for example.
    ```

    ---
    **II. Loading Resources as Clips: The Building Blocks**

    In this section we present the different sorts of clips and how to load them. For information on modifying a clip, see Modifying clips and apply effects. For how to put clips together see Compositing multiple clips. And for how to see/save theme, see Previewing and saving video clips.

    The following code summarizes the base clips that you can create with moviepy:
    ```python
    import numpy as np
    # Define some constants for later use (e.g., for ColorClip)
    black = (0, 0, 0) # RGB for black, assuming 0-255 range

    def frame_function(t):
        Random noise image of 200x100
        return np.random.randint(low=0, high=255, size=(100, 200, 3))

    def frame_function_audio(t):
        A note by producing a sinewave of 440 Hz
        return np.sin(440 * 2 * np.pi * t)
    ```

    **VIDEO CLIPS**
    ```python
    from moviepy.video.VideoClip import VideoClip, TextClip, ColorClip
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.ImageSequenceClip import ImageSequenceClip
    from moviepy.video.ImageClip import ImageClip

    # for custom animations, where frame_function is a function returning an image as numpy array for a given time
    clip = VideoClip(frame_function, duration=5)
    clip = VideoFileClip("example.mp4")  # for videos
    # for a list or directory of images to be used as a video sequence
    clip = ImageSequenceClip("example_img_dir", fps=24)
    clip = ImageClip("example.png")  # For a picture
    # To create the image of a text
    # When creating a TextClip, ensure font='path/to/font.ttf' is provided.
    clip = TextClip(font="./example.ttf", txt="Hello!", fontsize=70, color="black") # Use txt= keyword
    # a clip of a single unified color, where color is a RGB tuple/array/list
    clip = ColorClip(size=(460, 380), color=black).with_fps(24) # Set FPS for ColorClip using .with_fps()
    ```

    **AUDIO CLIPS**
    ```python
    from moviepy.audio.AudioClip import AudioClip, AudioArrayClip
    from moviepy.audio.io.AudioFileClip import AudioFileClip

    # for audio files, but also videos where you only want the keep the audio track
    clip = AudioFileClip("example.wav")
    # for custom audio, where frame_function is a function returning a
    # float (or tuple for stereo) for a given time
    clip = AudioClip(frame_function_audio, duration=3)
    ```
    The best to understand all these clips more thoroughly is to read the full documentation for each in the Api Reference.

    **Categories of video clips**

    Video clips are the building blocks of longer videos. Technically, they are clips with a `clip.get_frame(t)` method which outputs a HxWx3 numpy array representing the frame of the clip at time t.

    There are two main type of video clips:
    - animated clips (made with `VideoFileClip`, `VideoClip` and `ImageSequenceClip`), which will always have duration.
    - unanimated clips (made with `ImageClip`, `VideoClip`, `TextClip` and `ColorClip`), which show the same picture for an a-priori infinite duration.

    There are also special video clips called masks, which belong to the categories above but output greyscale frames indicating which parts of another clip are visible or not.

    A video clip can carry around an audio clip (`AudioClip`) in audio which is its soundtrack, and a mask clip in mask.

    **Animated clips**

    These are clips whose image will change over time, and which have a duration and a number of Frames Per Second.

    **VideoClip**
    `VideoClip` is the base class for all the other video clips in MoviePy. This class is practical when you want to make animations from frames that are generated by another library. All you need is to define a function `frame_function(t)` which returns a HxWx3 numpy array (of 8-bits integers) representing the frame at time t.

    Here is an example where we will create a pulsating red circle with graphical library Pillow.
    ```python
    from moviepy.video.VideoClip import VideoClip
    from PIL import Image, ImageDraw
    import numpy as np
    import math

    WIDTH, HEIGHT = (128, 128)
    RED = (255, 0, 0)

    def frame_function(t):
        frequency = 1  # One pulse per second
        coef = 0.5 * (1 + math.sin(2 * math.pi * frequency * t))  # radius varies over time
        radius = WIDTH * coef

        x1 = WIDTH / 2 - radius / 2
        y1 = HEIGHT / 2 - radius / 2
        x2 = WIDTH / 2 + radius / 2
        y2 = HEIGHT / 2 + radius / 2

        img = Image.new("RGB", (WIDTH, HEIGHT))
        draw = ImageDraw.Draw(img)
        draw.ellipse((x1, y1, x2, y2), fill=RED)

        return np.array(img)  # returns a 8-bit RGB array

    # we define a 2s duration for the clip to be able to render it later
    clip = VideoClip(frame_function, duration=2)
    # we must set a framerate because VideoClip have no framerate by default
    clip.write_gif("circle.gif", fps=15)
    ```
    **Note**
    Clips that are made with a `frame_function` do not have an explicit frame rate nor duration by default, so you must provide duration at clip creation and a frame rate (fps, frames per second) for `write_gif()` and `write_videofile()`, and more generally for any methods that requires iterating through the frames. For more, see VideoClip documentation.

    **VideoFileClip**
    A `VideoFileClip` is a clip read from a video file (most formats are supported) or a GIF file. This is probably one of the most used objects! You load the video as follows:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip

    myclip = VideoFileClip("example.mp4")

    # video file clips already have fps and duration
    # print("Clip duration: {}".format(myclip.duration)) # Avoid prints in final script
    # print("Clip fps: {}".format(myclip.fps))           # Use logging if needed

    myclip = myclip.subclip(0.5, 2)  # Cutting the clip between 0.5 and 2 secs. (using .subclip)
    # print("Clip duration: {}".format(myclip.duration))  # Cuting will update duration
    # print("Clip fps: {}".format(myclip.fps))  # and keep fps
    # the output video will be 1.5 sec long and use original fps
    myclip.write_videofile("result.mp4", codec="libx264", audio_codec="aac") # Always specify codecs
    ```
    **Note**
    These clips will have an fps (frame per second) and duration attributes, which will be transmitted if you do small modifications of the clip, and will be used by default in `write_gif()`, `write_videofile()`, etc. For more, see VideoFileClip documentation.

    **ImageSequenceClip**
    This `ImageSequenceClip` is a clip made from a series of images:
    ```python
    from moviepy.video.ImageSequenceClip import ImageSequenceClip
    import os

    # A clip with a list of images showed for 1 second each
    myclip = ImageSequenceClip(
        [
            "example_img_dir/image_0001.jpg",
            "example_img_dir/image_0002.jpg",
            "example_img_dir/image_0003.jpg",
        ],
        durations=[1, 1, 1],
    )
    # 3 images, 1 seconds each, duration = 3
    # print("Clip duration: {}".format(myclip.duration))
    # 3 seconds, 3 images, fps is 3/3 = 1
    # print("Clip fps: {}".format(myclip.fps))

    # This time we will load all images in the dir, and instead of showing theme
    # for X seconds, we will define FPS
    myclip2 = ImageSequenceClip("./example_img_dir", fps=30)
    # fps = 30, so duration = nb images in dir / 30
    # print("Clip duration: {}".format(myclip2.duration))
    # print("Clip fps: {}".format(myclip2.fps))  # fps = 30

    # the gif will be 30 fps, its duration will depend on the number of
    # images in dir
    myclip.write_gif("result.gif", fps=1)  # specify fps for gif
    myclip2.write_gif("result2.gif", fps=30) # specify fps for gif
    ```
    When creating an image sequence, `sequence` can be either a list of image names (that will be played in the provided order), a folder name (played in alphanumerical order), or a list of frames (Numpy arrays), obtained for instance from other clips.

    **Warning**
    All the images in list/folder/frames must be of the same size, or an exception will be raised. For more, see ImageSequenceClip documentation.

    **DataVideoClip**
    `DataVideoClip` is a video clip that takes a list of datasets, a callback function, and makes each frame by iterating over the dataset and invoking the callback function with the current data as the first argument. You will probably never use this in general tasks.
    ```python
    from moviepy.video.DataVideoClip import DataVideoClip
    import numpy as np

    Let's make a clip where frames depend on values in a list
    dataset = [
        (255, 0, 0), (0, 255, 0), (0, 0, 255),
        (0, 255, 255), (255, 0, 255), (255, 255, 0),
    ]

    def frame_function(data):
        frame = np.full((100, 200, 3), data, dtype=np.uint8)
        return frame

    myclip = DataVideoClip(data=dataset, data_to_frame=frame_function, fps=2)
    myclip.write_videofile("result.mp4", fps=30, codec="libx264", audio_codec="aac")
    ```
    For more, see DataVideoClip documentation.

    **UpdatedVideoClip**
    **Warning**: This is really advanced usage, you will probably never need it. `UpdatedVideoClip` is a video whose `frame_function` requires some objects to be updated before it can compute it.
    ```python
    from moviepy.video.UpdatedVideoClip import UpdatedVideoClip
    import random
    import numpy as np

    class CoinFlipWorld:
        def __init__(self, fps):
            self.clip_t = 0
            self.win_strike = 0
            self.reset = False
            self.fps = fps

        def update(self):
            if self.reset:
                self.win_strike = 0
                self.reset = False
            choice = random.randint(0, 1)
            face = random.randint(0, 1)
            if choice == face:
                self.win_strike += 1
                return
            self.reset = True
            self.clip_t += 1 / self.fps

        def to_frame(self):
            red_intensity = 255 * (self.win_strike / 10)
            red_intensity = min(red_intensity, 255)
            return np.full((100, 200, 3), (red_intensity, 0, 0), dtype=np.uint8)

    world = CoinFlipWorld(fps=5)
    myclip = UpdatedVideoClip(world=world, duration=10)
    myclip.write_videofile("result.mp4", fps=5, codec="libx264", audio_codec="aac")
    ```

    **Unanimated clips**

    These are clips whose image will, at least before modifications, stay the same. By default they have no duration nor FPS, meaning you will need to define them before doing operations needing such information (for example, rendering).

    **ImageClip**
    `ImageClip` is the base class for all unanimated clips, it’s a video clip that always displays the same image. Along with `VideoFileClip` it’s one of the most used kind of clip. You can create one as follows:
    ```python
    from moviepy.video.ImageClip import ImageClip
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import numpy as np

    Here's how you transform a VideoClip into an ImageClip from an image, from
    arbitrary data, or by extracting a frame at a given time

    noise_image = np.random.randint(low=0, high=255, size=(100, 200, 3))

    myclip1 = ImageClip("example.png")  # You can create it from a path
    myclip2 = ImageClip(noise_image)  # from a (height x width x 3) RGB numpy array
    # Or load videoclip and extract frame at a given time
    myclip3 = VideoFileClip("./example.mp4").to_ImageClip(t="00:00:01")
    ```
    For more, see ImageClip documentation.

    **TextClip**
    A `TextClip` is a clip that will turn a text string into an image clip. `TextClip` accepts many parameters, letting you configure the appearance of the text, such as font and font size, color, interlining, text alignment, etc. The font you want to use must be an OpenType font, and you will set it by passing the path to the font file.
    ```python
    from moviepy.video.VideoClip import TextClip
    import os # Assuming font file needs path

    font = "./example.ttf" # Ensure this font path is valid in the execution environment

    # First we use as string and let system autocalculate clip dimensions to fit the text
    # we set clip duration to 2 secs, if we do not, it got an infinite duration
    txt_clip1 = TextClip(
        font=font,
        txt="Hello World !", # Use txt= keyword
        fontsize=30,
        color="#FF0000",  # Red
        bg_color="#FFFFFF",
        duration=2,
    )
    # This time we load text from a file, we set a fixed size for clip and let the system find best font size,
    # allowing for line breaking
    txt_clip2 = TextClip(
        font=font,
        filename="./example.txt",
        size=(500, 200),
        bg_color="#FFFFFF",
        method="caption",
        color=(0, 0, 255, 127),
    )  # Blue with 50% transparency

    # we set duration, because by default image clip are infinite, and we cannot render infinite
    txt_clip2 = txt_clip2.with_duration(2)
    # ImageClip have no FPS either, so we must defined it
    txt_clip1.write_videofile("result1.mp4", fps=24, codec="libx264", audio_codec="aac")
    txt_clip2.write_videofile("result2.mp4", fps=24, codec="libx264", audio_codec="aac")
    ```
    **Note**
    The parameter `method` lets you define if text should be written and overflow if too long (`label`) or be automatically broken over multiple lines (`caption`). For a more detailed explanation of all the parameters, see TextClip documentation.

    **ColorClip**
    A `ColorClip` is a clip that will return an image of only one color. It is sometimes useful when doing compositing.
    ```python
    from moviepy.video.VideoClip import ColorClip

    # Color is passed as a RGB tuple
    myclip = ColorClip(size=(200, 100), color=(255, 0, 0), duration=1).with_fps(24) # Set FPS
    myclip.write_videofile("result.mp4", fps=24, codec="libx264", audio_codec="aac")
    ```
    For more, see ColorClip documentation.

    **Mask clips**
    Masks are a special kind of `VideoClip` with the property `is_mask` set to `True`. They can be attached to any other kind of `VideoClip` through method `with_mask()`.
    ```python
    from moviepy.video.VideoClip import VideoClip
    from moviepy.video.ImageClip import ImageClip
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import numpy as np

    # Random RGB noise image of 200x100
    frame_function = lambda t: np.random.rand(100, 200)

    # To define the VideoClip as a mask, just pass parameter is_mask as True
    maskclip1 = VideoClip(frame_function, duration=4, is_mask=True).with_fps(24) # Set FPS for maskclip
    maskclip2 = ImageClip("example_mask.jpg", is_mask=True)  # A fixed mask as jpeg
    maskclip3 = VideoFileClip("example_mask.mp4", is_mask=True)  # A video as a mask

    # Load our basic clip, resize to 200x100 and apply each mask
    clip = VideoFileClip("example.mp4").resized(width=200, height=100) # Use .resized()
    clip_masked1 = clip.with_mask(maskclip1)
    clip_masked2 = clip.with_mask(maskclip2)
    clip_masked3 = clip.with_mask(maskclip3)
    ```
    **Note**
    In the case of video and image files, if these are not already black and white they will be converted automatically. Also, when you load an image with an alpha layer, like a PNG, MoviePy will use this layer as a mask unless you pass `transparent=False`.

    Any video clip can be turned into a mask with `to_mask()`, and a mask can be turned to a standard RGB video clip with `to_RGB()`.

    Masks are treated differently by many methods (because their frames are different) but at the core, they are `VideoClip`, so you can do with them everything you can do with a video clip: modify, cut, apply effects, save, etc.

    **Using audio elements with audio clips**

    In addition to `VideoClip` for visual, you can use audio elements, like an audio file, using the `AudioClip` class. Both are quite similar, except `AudioClip` method `get_frame()` return a numpy array of size Nx1 for mono, and size Nx2 for stereo.

    **AudioClip**
    `AudioClip` is the base class for all audio clips. If all you want is to edit audio files, you will never need it. All you need is to define a function `frame_function(t)` which returns a Nx1 or Nx2 numpy array representing the sound at time t.
    ```python
    from moviepy.audio.AudioClip import AudioClip
    import numpy as np

    def audio_frame(t):
        Producing a sinewave of 440 Hz -> note A
        return np.sin(440 * 2 * np.pi * t)

    audio_clip = AudioClip(frame_function=audio_frame, duration=3)
    ```
    For more, see AudioClip documentation.

    **AudioFileClip**
    `AudioFileClip` is used to load an audio file. This is probably the only kind of audio clip you will use. You simply pass it the file you want to load:
    ```python
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    clip = AudioFileClip("example.wav")
    clip.write_audiofile("./result.wav")
    ```
    For more, see AudioFileClip documentation.

    **AudioArrayClip**
    `AudioArrayClip` is used to turn an array representing a sound into an audio clip. You will probably never use it, unless you need to use the result of some third library without using a temporary file. You need to provide a numpy array representing the sound (of size Nx1 for mono, Nx2 for stereo), and the number of fps, indicating the speed at which the sound is supposed to be played.
    ```python
    from moviepy.audio.AudioClip import AudioArrayClip
    import numpy as np

    Let's create an audioclip from values in a numpy array

    notes = {"A": 440, "B": 494, "C": 523, "D": 587, "E": 659, "F": 698}
    note_duration = 0.5
    sample_rate = 44100  # Number of samples per second

    def frame_function(t, note_frequency):
        return np.sin(note_frequency * 2 * np.pi * t)

    audio_frame_values = [
        2 * [frame_function(t, freq)]
        for freq in notes.values()
        for t in np.arange(0, note_duration, 1.0 / sample_rate)
    ]
    audio_clip = AudioArrayClip(np.array(audio_frame_values), fps=sample_rate)
    audio_clip.write_audiofile("result.wav", fps=44100)
    ```
    For more, see AudioArrayClip documentation.

    ---
    **III. Modifying Clips and Applying Effects**

    Once you have loaded a Clip, the next step is to modify it to integrate it into your final video. There are three main ways to modify a clip:
    1.  The built-in methods of `VideoClip` or `AudioClip` modifying the properties of the object.
    2.  The already-implemented effects of MoviePy you can apply on clips, usually by applying filters on each frame of the clip at rendering time.
    3.  The transformation filters that you can apply using `transform()` and `time_transform()`.

    **How modifications are applied to a clip?**

    **Clip copy during modification**
    When modifying a clip, MoviePy will never modify that clip directly. Instead it will return a modified copy of the original and leave the original untouched. This is known as out-of-place instead of in-place behavior.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import moviepy.audio.fx as afx
    import moviepy.video.fx as vfx

    clip = VideoFileClip("example.mp4")

    # This does nothing, as .with_volume_scaled returns a copy of clip which is lost
    clip.with_volume_scaled(0.1)

    # This creates a copy of clip in clip_whisper with a volume of only 10% the original,
    # but does not modify the original clip
    clip_whisper = clip.with_volume_scaled(0.1)

    # This replaces the original clip with a copy of it where volume is only 10% of
    # the original.
    clip = clip.with_volume_scaled(0.1)
    ```
    This is an important point to understand, because it is one of the most recurrent source of bugs for newcomers.

    **Memory consumption of effect and modifications**
    When applying an effect or modification, it does not immediately apply the effect to all the frames of the clip, but only to the first frame: all the other frames will only be modified when required (that is, when you will write the whole clip to a file or when you will preview it). This means that creating a new clip is neither time nor memory hungry; all the computation happens during the final rendering.

    **Time representations in MoviePy**
    Many methods accept duration or timepoint as arguments. For instance `clip.subclip(t_start, t_end)` which cuts the clip between two timepoints. MoviePy usually accepts duration and timepoint as either:
    - a number of seconds as a float.
    - a tuple with (minutes, seconds) or (hours, minutes, seconds).
    - a string such as `'00:03:50.54'`.

    Also, you can usually provide negative times, indicating a time from the end of the clip. For example, `clip.subclip(-20, -10)` cuts the clip between 20s before the end and 10s before the end.

    **Modify a clip using the `with_` methods**
    The first way to modify a clip is by modifying internal properties of your object, thus modifying its behavior. These methods usually start with the prefix `with_` or `without_`, indicating that they will return a copy of the clip with the properties modified.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    myclip = VideoFileClip("example.mp4")
    myclip = myclip.with_end(5)  # stop the clip after 5 sec
    myclip = myclip.without_audio()  # remove the audio of the clip
    ```
    In addition to the `with_*` methods, a handful of very common methods are also accessible under shorter names: `resized()`, `cropped()`, `rotated()`. For a list of all those methods, see `Clip` and `VideoClip` documentation.

    **Modify a clip using effects**
    The second way to modify a clip is by using effects that will modify the frames of the clip (which internally are no more than numpy arrays) by applying some sort of functions on them. MoviePy comes with many effects implemented in `moviepy.video.fx` for visual effects and `moviepy.audio.fx` for audio effects. For practicality, these two modules are loaded in MoviePy as `vfx` and `afx`.
    To use these effects, you simply need to instantiate them as object and apply them on your Clip using method `with_effects()`, with a list of Effect objects you want to apply.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import moviepy.video.fx as vfx
    import moviepy.audio.fx as afx

    myclip = VideoFileClip("example.mp4")
    # resize clip to be 460px in width, keeping aspect ratio
    myclip = myclip.with_effects([vfx.Resize(width=460)])

    # fx method return a copy of the clip, so we can easily chain them
    # double the speed and half the audio volume
    myclip = myclip.with_effects([vfx.MultiplySpeed(2), afx.MultiplyVolume(0.5)])

    # This darken the clip. Note that some effects have a direct shortcut.
    # myclip = myclip.with_effects([vfx.MultiplyColor(0.5)])
    ```
    **Note**
    MoviePy effects are automatically applied to both the sound and the mask of the clip if it is relevant, so that you don’t have to worry about modifying these. For a list of those effects, see `moviepy.video.fx` and `moviepy.audio.fx` documentation. In addition to the effects already provided by MoviePy, you can obviously create your own effects and use them the same way.

    **Modify a clip appearance and timing using filters**
    In addition to modifying a clip’s properties and using effects, you can also modify the appearance or timing of a clip by using your own custom filters with `time_transform()`, `image_transform()`, and more generally with `transform()`. All these methods work by taking as first parameter a callback function that will receive either a clip frame, a timepoint, or both, and return a modified version of these.

    **Modify only the timing of a Clip**
    You can change the timeline of the clip with `time_transform(your_filter)`. Where `your_filter` is a callback function taking clip time as a parameter and returning a new time:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import math

    my_clip = VideoFileClip("example.mp4")
    # Let's accelerate the video by a factor of 3
    modified_clip1 = my_clip.time_transform(lambda t: t * 3)
    # Let's play the video back and forth with a "sine" time-warping effect
    modified_clip2 = my_clip.time_transform(lambda t: 1 + math.sin(t))
    ```
    **Note**
    By default `time_transform()` will only modify the clip main frame, without modifying clip audio or mask for `VideoClip`. If you wish to also modify audio and/or mask you can provide the parameter `apply_to` with either `'audio'`, `'mask'`, or `['audio', 'mask']`.

    **Modifying only the appearance of a Clip**
    For `VideoClip`, you can change the appearance of the clip with `image_transform(your_filter)`. Where `your_filter` is a callback function, taking clip frame (a numpy array) as a parameter and returning the transformed frame:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import numpy as np

    Let's invert the green and blue channels of a video
    my_clip = VideoFileClip("example.mp4")

    def invert_green_blue(image: np.ndarray) -> np.ndarray:
        return image[:, :, [0, 2, 1]]

    modified_clip1 = my_clip.image_transform(invert_green_blue)
    ```
    **Note**
    You can define if transformation should be applied to audio and mask same as for `time_transform()`. Sometimes need to treat clip frames and mask frames in a different way. To distinguish between the two, you can always look at their shape, clips are HW3, and masks H*W.

    **Modifying both the appearance and the timing of a Clip**
    You may want to process the clip by taking into account both the time and the frame picture, for example to apply visual effects varying with time. This is possible with the method `transform(your_filter)`. Where `your_filter` is a callback function taking two parameters, and returning a new frame picture. The first argument is a `get_frame` method (i.e., a function `get_frame(time)` which given a time returns the clip’s frame at that time), and the second argument is the time.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import numpy as np # required for image manipulation in filter

    Let's create a scolling video effect from scratch
    my_clip = VideoFileClip("example.mp4")

    def scroll(get_frame, t):
        
        This function returns a 'region' of the current frame.
        The position of this region depends on the time.
        
        frame = get_frame(t)
        frame_region = frame[int(t) : int(t) + 360, :]
        return frame_region

    modified_clip1 = my_clip.transform(scroll)
    ```
    **Note**
    You can define if transformation should be applied to audio and mask same as for `time_transform()`. When programming a new effect, whenever it is possible, prefer using `time_transform` and `image_transform` instead of `transform` when implementing new effects. The reason is that, though they both internally rely on `transform` when these effects are applied to `ImageClip` objects, MoviePy will recognize they only need to be applied once instead of on each frame, resulting in faster renderings. To keep things simple, we have only addressed the case of `VideoClip`, but know that the same principle applies to `AudioClip`, except that instead of a picture frame, you will have an audio frame, which is also a numpy array.

    **Creating your own effects**
    In addition to the existing effects already offered by MoviePy, we can create our own effects to modify a clip however we want. For more complex and reusable clip modifications, we can create our own custom effects, that we will later apply with `with_effects()`.
    In MoviePy, effects are objects of type `moviepy.Effect.Effect`, which is the base abstract class for all effects. To create an effect, inherit the `Effect` class, create an `__init__` method, and implement the inherited `apply()` method, which takes the clip to modify and returns the modified version.
    ```python
    from moviepy.decorators import requires_duration
    from moviepy.video.VideoClip import VideoClip
    import numpy as np

    @requires_duration
    def progress_bar(clip: VideoClip, color: tuple, height: int = 10):
        
        Add a progress bar at the bottom of our clip

        Parameters
        ----------
        color: Color of the bar as a RGB tuple
        height: The height of the bar in pixels. Default = 10
        

        def filter(get_frame, t):
            progression = t / clip.duration
            bar_width = int(progression * clip.w)
            frame = get_frame(t)
            frame[-height:, 0:bar_width] = color
            return frame

        return clip.transform(filter, apply_to="mask")
    ```
    **Note**
    When creating an effect, you frequently have to write boilerplate code for assigning properties on object initialization; dataclasses is a nice way to limit that. If you want to create your own effects, in addition of this documentation we strongly encourage you to go and take a look at the existing ones (see `moviepy.video.fx` and `moviepy.audio.fx`) to see how they works and take inspiration.

    ---
    **IV. Compositing Multiple Clips**

    Video composition, also known as non-linear editing, is the act of mixing and playing several clips together in a new clip.

    **Note**
    Before starting, note that video clips generally carry an audio track and a mask, which are also clips. When you compose these clips together, the soundtrack and mask of the final clip are automatically generated by putting together the soundtracks and masks of the clips. So most of the time you don’t need to worry about mixing the audio and masks.

    **Juxtaposing and concatenating clips**
    Two simple ways of putting clips together is to concatenate them (to play them one after the other in a single long clip) or to juxtapose them (to put them side by side in a single larger clip).

    **Concatenating multiple clips**
    Concatenation can be done very easily with the function `concatenate_videoclips()`.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.editor import concatenate_videoclips

    Let's concatenate (play one after the other) three video clips

    clip1 = VideoFileClip("example.mp4")
    clip2 = VideoFileClip("example2.mp4").subclip(0, 1) # Use .subclip()
    clip3 = VideoFileClip("example3.mp4")

    final_clip = concatenate_videoclips([clip1, clip2, clip3])
    final_clip.write_videofile("final_clip.mp4", codec="libx264", audio_codec="aac")
    ```
    **Note**
    The clips do not need to be the same size. If they aren’t, they will all appear centered in a clip large enough to contain the biggest of them, with optionally a color of your choosing to fill the background. For more info, see concatenate_videoclips documentation.

    **Juxtaposing multiple clips**
    Putting multiple clip side by side is done with `clips_array()`:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.editor import clips_array
    import moviepy.video.fx as vfx

    Let's juxtapose four video clips in a 2x2 grid

    clip1 = VideoFileClip("example.mp4").with_effects([vfx.Margin(10)])  # add 10px contour
    clip2 = clip1.with_effects([vfx.MirrorX()])  # Flip horizontaly
    clip3 = clip1.with_effects([vfx.MirrorY()])  # Flip verticaly
    clip4 = clip1.resized(0.6)  # downsize to 60% of original

    # The form of the final clip will depend of the shape of the array
    array = [
        [clip1, clip2],
        [clip3, clip4],
    ]
    final_clip = clips_array(array)
    final_clip = final_clip.resized(width=480) # Use .resized()

    final_clip.write_videofile("final_clip.mp4", codec="libx264", audio_codec="aac")
    ```
    For more info, see clip_array documentation.

    **More complex video compositing**
    The `CompositeVideoClip` class is the base of all video compositing. For example, internally, both `concatenate_videoclips()` and `clips_array()` create a `CompositeVideoClip`. It provides a very flexible way to compose clips, by playing multiple clip on top of each other, in the order they have been passed to `CompositeVideoClip`.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip

    Let's stack three video clips on top of each other with CompositeVideoClip

    clip1 = VideoFileClip("example.mp4")
    clip2 = VideoFileClip("example2.mp4").subclip(0, 1) # Use .subclip()
    clip3 = VideoFileClip("example.mp4")

    final_clip = CompositeVideoClip([clip1, clip2, clip3])
    final_clip.write_videofile("final_clip.mp4", codec="libx264", audio_codec="aac")
    ```
    Now `final_clip` plays all clips at the same time, with clip3 over clip2 over clip1. It means that, if all clips have the same size, then only clip3, which is on top, will be visible in the video… Unless clip3 and/or clip2 have masks which hide parts of them.

    **Note**
    By default the composition has the size of its first clip (as it is generally a background). But sometimes you will want to make your clips float in a bigger composition. To do so, just pass the size of the final composition as size parameter of `CompositeVideoClip`. For more info, see CompositeVideoClip documentation.

    **Changing starting and stopping times of clips**
    In a `CompositeVideoClip`, each clip starts to play at a time that is specified by its `clip.start` attribute, and will play until `clip.end`.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip

    clip1 = VideoFileClip("example.mp4")
    clip2 = VideoFileClip("example2.mp4").subclip(0, 1) # Use .subclip()
    clip3 = VideoFileClip("example3.mp4")

    clip1 = clip1.with_end(1) # stop the clip after 1s
    clip2 = clip2.with_start(1.5) # play clip2 after 1.5s
    clip3 = clip3.with_start(clip2.end).with_duration(1) # play clip3 at the end of clip2, and so for 3 seconds only

    final_clip = CompositeVideoClip([clip1, clip2, clip3])
    final_clip.write_videofile("final_clip.mp4", codec="libx264", audio_codec="aac")
    ```
    **Note**
    When working with timing of your clip, you will frequently want to keep only parts of the original clip. To do so, you should take a look at `subclip()` and `with_section_cut_out()` documentation.

    **Positioning clips**
    Frequently, you will want a smaller clip to appear on top of a larger one, and decide where it will appear in the composition by setting their position. You can do so by using the `with_position()` method. The position is always defined from the top left corner, but you can define it in many ways:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.VideoClip import TextClip
    from moviepy.video.ImageClip import ImageClip
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
    import os # For font/image paths

    background = VideoFileClip("example2.mp4").subclip(0, 2) # Use .subclip()
    title = TextClip(
        font="./example.ttf", txt="Big Buck Bunny", fontsize=80, color="#fff",
        align="center", duration=1, # Use align instead of text_align
    )
    author = TextClip(
        font="./example.ttf", txt="Blender Foundation", fontsize=40, color="#fff",
        align="center", duration=1,
    )
    copyright_text = TextClip( # Renamed to avoid conflict with 'copyright' keyword
        font="./example.ttf", txt="© CC BY 3.0", fontsize=20, color="#fff",
        align="center", duration=1,
    )
    logo = ImageClip("./example2.png", duration=1).resized(height=50)

    title = title.with_position(("center", 0.25), relative=True)

    top = background.h * 0.25 + title.h + 30
    left = (background.w - author.w) / 2
    author = author.with_position((left, top))

    copyright_text = copyright_text.with_position(("center", background.h - copyright_text.h - 30))

    top = (background.h - logo.h) / 2
    logo = logo.with_position(lambda t: ("center", top + t * 30))

    final_clip = CompositeVideoClip([background, title, author, copyright_text, logo])
    final_clip.write_videofile("final_clip.mp4", codec="libx264", audio_codec="aac")
    ```
    **Note**
    The position is a tuple with horizontal and vertical position. You can give them as pixels, as strings (`"center"`, `"left"`, `"right"`, `"top"`, `"bottom"`), and even as a percentage by providing a float and passing the argument `relative=True`.

    **Adding transitions effects**
    The last part of composition is adding transition effects. For example, when a clip starts while another is still playing, it would be nice to make the new one fade-in instead of showing abruptly. To do so, we can use the transitions offered by MoviePy in `transitions`, like `CrossFadeIn()`:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
    import moviepy.video.fx as vfx

    clip1 = VideoFileClip("example.mp4")
    clip2 = VideoFileClip("example2.mp4")

    clips = [
        clip1.with_end(2),
        clip2.with_start(1).with_effects([vfx.CrossFadeIn(1)]),
    ]
    final_clip = CompositeVideoClip(clips)
    final_clip.write_videofile("final_clip.mp4", codec="libx264", audio_codec="aac")
    ```
    MoviePy offers only few transitions in `transitions`. But technically, transitions are mostly effects applied to the mask of a clip! That means you can actually use any of the already existing effects, and use them as transitions by applying them on the mask of your clip. For more info, see `transitions` and `moviepy.video.fx` documentation.

    **Compositing audio clips**
    When you mix video clips together, MoviePy will automatically compose their respective audio tracks to form the audio track of the final clip, so you don’t need to worry about compositing these tracks yourself. If you want to make a custom audio track from several audio sources, audio clips can be mixed together like video clips, with `CompositeAudioClip` and `concatenate_audioclips()`:
    ```python
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from moviepy.video.compositing.CompositeAudioClip import CompositeAudioClip
    from moviepy.editor import concatenate_audioclips
    import moviepy.audio.fx as afx

    clip1 = AudioFileClip("example.wav")
    clip2 = AudioFileClip("example2.wav")
    clip3 = AudioFileClip("example3.wav")

    concat = concatenate_audioclips([clip1, clip2, clip3])

    compo = CompositeAudioClip(
        [
            clip1.with_volume_scaled(1.2),
            clip2.with_start(5),
            clip3.with_start(9),
        ]
    )
    ```

    ---
    **V. Previewing and Saving Video Clips**

    Once you are done working with your clips, the last step will be to export the result into a video/image file, or sometimes to simply preview it in order to verify everything is working as expected.

    **Previewing a clip**
    When you are working with a clip, you will frequently need to have a peek at what your clip looks like, either to verify that everything is working as intended, or to check how things look. To do so you could render your entire clip into a file, but that’s a pretty long task, and you only need a quick look, so a better solution exists: previewing.

    **Preview a clip as a video**
    **Warning**
    You must have `ffplay` installed and accessible to MoviePy to be able to use `preview()`.
    The first thing you can do is to preview your clip as a video, by calling method `preview()` on your clip:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip

    myclip = VideoFileClip("./example.mp4").subclip(0, 1)  # Keep only 0 to 1 sec

    myclip.preview()
    myclip.preview(fps=5, audio_fps=11000)
    myclip.preview(audio=False)
    ```
    You will probably frequently want to preview only a small portion of your clip, though `preview` does not offer such capabilities, you can easily emulate such behavior by using `subclip()`.

    **Note**
    It is quite frequent for a clip preview to be out of sync, or to play slower than it should. It means that your computer is not powerful enough to render the clip in real time. Don’t hesitate to play with the options of preview: for instance, lower the `fps` of the sound (11000 Hz is still fine) and the video. Also, downsizing your video with `resized` can help. For more info, see `preview()` documentation.

    A quite similar function is also available for `AudioClip()`, see `ffplay_audiopreview()`.

    **Preview just one frame of a clip**
    In a lot of situations, you don’t really need to preview your entire clip, seeing only one frame is enough to see how it looks like and to make sure everything goes as expected. To do so, you can use the method `show()` on your clip, passing the frame time as an argument:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip

    myclip = VideoFileClip("./example.mp4")

    myclip.show()
    myclip.show(1.5)
    myclip.show(1.5, with_mask=False)
    ```
    Contrary to video previewing, `show` does not require ffplay, but uses Pillow's `Image.show` function. For more info, see `show()` documentation.

    **Showing a clip in Jupyter Notebook**
    If you work with a Jupyter Notebook, it can be very practical to display your clip in the notebook. To do so, you can use the method `display_in_notebook()` on your clip.
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.ImageClip import ImageClip
    from moviepy.audio.io.AudioFileClip import AudioFileClip

    my_video_clip = VideoFileClip("./example.mp4")
    my_image_clip = ImageClip("./example.png")
    my_audio_clip = AudioFileClip("./example.wav")

    # This function is primarily for Jupyter Notebooks, not for general scripts.
    # It directly embeds the clip; do not call it in scripts intended for file output.
    # my_video_clip.display_in_notebook()
    # my_image_clip.display_in_notebook()
    # my_audio_clip.display_in_notebook()

    # my_video_clip.display_in_notebook(t=1)
    # my_video_clip.display_in_notebook(width=400)
    # my_video_clip.display_in_notebook(autoplay=1, loop=1)
    ```
    **Warning**
    Know that `display_in_notebook()` will only work if it is on the last line of a notebook cell. Also, note that `display_in_notebook()` actually embeds the clips physically in your notebook. The advantage is that you can move the notebook or put it online and the videos will work. The drawback is that the file size of the notebook can become very large. For more info, see `display_in_notebook()` documentation.

    **Save your clip into a file**
    Once you are satisfied with how your clip looks, you can save it into a file, a step known in video editing as rendering. MoviePy offers various ways to save your clip.

    **Video files (.mp4, .webm, .ogv…)**
    The obvious first choice will be to write your clip to a video file, which you can do with `write_videofile()`:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    from moviepy.video.VideoClip import TextClip
    from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
    import os

    background = VideoFileClip("long_examples/example2.mp4").subclip(0, 10) # Use .subclip()
    title = TextClip(
        font="./example.ttf", txt="Big Buck Bunny", fontsize=80, color="#fff",
        align="center", duration=3,
    ).with_position(("center", "center"))

    final_clip = CompositeVideoClip([background, title])

    final_clip.write_videofile("result.mp4", codec="libx264", audio_codec="aac")
    final_clip.write_videofile("result24fps.mp4", fps=24, codec="libx264", audio_codec="aac")

    final_clip.write_videofile(
        "result.webm", codec="libvpx-vp9", fps=24, preset="ultrafast", threads=4, audio_codec="aac"
    )
    ```
    MoviePy can find a default codec name for the most common file extensions. If you want to use exotic formats or if you are not happy with the defaults you can provide the codec with `codec='mpeg4'` for instance. There are many many options when you are writing a video (bitrate, parameters of the audio writing, file size optimization, number of processors to use, etc.). For more info, see `write_videofile()` documentation.

    **Note**
    Though you are encouraged to play with settings of `write_videofile`, know that lowering the optimization preset or increasing the number of threads will not necessarily improve the rendering time, as the bottleneck may be on MoviePy computation of each frame and not in ffmpeg encoding. Also, know that it is possible to pass additional parameters to `ffmpeg` command line invoked by MoviePy by using the `ffmpeg_params` argument.

    Sometimes it is impossible for MoviePy to guess the duration attribute of the clip (keep in mind that some clips, like ImageClips displaying a picture, have a priori an infinite duration). Then, the duration must be set manually with `with_duration()`:
    ```python
    from moviepy.video.ImageClip import ImageClip
    # By default an ImageClip has no duration
    my_clip = ImageClip("example.png")

    # This will fail without duration!
    # try:
    #     my_clip.write_videofile("result.mp4")
    # except Exception as e:
    #     print("Cannot write a video without duration: {}".format(e)) # Use logging in production

    # By calling with_duration on our clip, we fix the problem! We also need to set fps
    my_clip.with_duration(2).write_videofile("result.mp4", fps=1, codec="libx264", audio_codec="aac")
    ```
    **Note**
    A quite similar function is also available for `AudioClip()`, see `write_audiofile()` documentation.

    **Export a single frame of the clip**
    As for previewing, sometimes you will need to export only one frame of a clip, for example to create the preview image of a video. You can do so with `save_frame()`:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    myclip = VideoFileClip("example.mp4")
    myclip.save_frame("result.png", t=1)  # Save frame at 1 sec
    ```
    For more info, see `save_frame()` documentation.

    **Animated GIFs**
    In addition to writing video files, MoviePy also lets you write GIF files with `write_gif()`:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    myclip = VideoFileClip("example.mp4").subclip(0, 2) # Use .subclip()

    myclip.write_gif("result.gif", fps=10) # Specify fps
    ```
    For more info, see `write_gif()` documentation.

    **Export all the clip as images in a directory**
    Lastly, you may wish to export an entire clip as an image sequence (multiple images in one directory, one image per frame). You can do so with the function `write_images_sequence()`:
    ```python
    from moviepy.video.io.VideoFileClip import VideoFileClip
    import os

    myclip = VideoFileClip("example.mp4")

    # Create output directory if it doesn't exist
    output_dir = "./output_images"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    myclip.write_images_sequence(os.path.join(output_dir, "%d.jpg")) # Use os.path.join
    myclip.write_images_sequence(os.path.join(output_dir, "%04d.jpg"), fps=10) # Specify fps and path format
    ```
    For more info, see `write_images_sequence()` documentation.

    ---
    **VII. Updating from v1.X to v2.X**

    MoviePy v2.0 has undergone some large changes with the aim of making the API more consistent and intuitive. In order to do so multiple breaking changes have been made. Therefore, there is a high likelihood that your pre-v2.0 programs will not run without some changes.

    **Dropping support of Python 2**
    Starting with version 2.0, MoviePy no longer supports Python 2, since Python 2 reached its end of life in 2020. Focusing on Python 3.7+ allows MoviePy to take advantage of the latest language features and improvements while maintaining code quality and security. Users are encouraged to upgrade to a supported version of Python to continue using MoviePy.

    **`moviepy.editor` suppression and simplified importation**
    Before v2.0, it was advised to import from `moviepy.editor` whenever you needed to do some sort of manual operations, such as previewing or hand editing, because the editor package handled a lot of magic and initialization, making your life easier, at the cost of initializing some complex modules like pygame.
    With version 2.0, the `moviepy.editor` namespace simply no longer exists (for most core classes). You simply import everything from MoviePy like this (though specific imports are preferred for clarity):
    ```python
    # from moviepy import * # This style is often discouraged for clarity but shown in some examples
    from moviepy.video.io.VideoFileClip import VideoFileClip # You can also import only the things you really need
    ```
    **Renaming and API unification**
    One of the most significant changes has been renaming all `.set_` methods to `.with_`. More generally, almost all the methods modifying a clip now start by `with_`, indicating that they work ‘out-of-place’, meaning they do not directly modify the clip, but instead copy it, modify this copy, and return the updated copy, leaving the original clip untouched. We advise you to check in your code for any call of method from `Clip` objects and check for a matching `.with_` equivalent.

    **Massive refactoring of effects**
    With version 2.0, effects have undergone massive changes and refactoring. Though the logic of why and when applying effects remain globally the same, the implementation changed quite heavily. If you used any kind of effects, you will have to update your code!

    **Moving effects from function to classes**
    MoviePy version 2.0 introduces a more structured and object-oriented approach to handling effects. In previous versions, effects were simply Python functions that manipulated video clips or images. However, in version 2.0 and onwards, effects are now represented as classes. This shift allows for better organization, encapsulation, and reusability of code, as well as more comprehensible code. Each effect is now encapsulated within its own class, making it easier to manage and modify. All effects are now implementing the `Effect` abstract class, so if you ever used any custom effect, you will have to migrate to the new object implementation. For more info see Creating your own effects.

    **Moving from `clip.fx` to `with_effects()`**
    Moving from function to object also meant MoviePy had to drop the method `Clip.fx` previously used to apply effects in favor of the new `with_effects()`. For more info about how to use effects with v2.0, see Modify a clip using effects.

    **Removing effects as clip methods**
    Before version 2.0, when importing from `moviepy.editor` the effects were added as clip class method at runtime. This is no longer the case. If you previously used effect by calling them as clips method, you must now use `with_effects()`.

    **Dropping many external dependencies and unifying environment**
    With v1.0, MoviePy relied on many optional external dependencies, trying to gracefully fallback from one library to another in the event one of them was missing. This resulted in complex and hard to maintain code for the MoviePy team, as well as fragmented and hard to understand environment for the users. With v2.0 the MoviePy team tried to offer a simpler, smaller and more unified dependency list, with focusing on Pillow for all complex image manipulation, and dropping altogether the usage of ImageMagick, PyGame, OpenCV, SciPy, Scikit, and a few others.

    **Removed features**
    Sadly, reducing the scope of MoviePy and limiting the external libraries means that some features had to be removed. If you used any of the following features, you will have to create your own replacement: `moviepy.video.tools.tracking`, `moviepy.video.tools.segmenting`, `moviepy.video.io.sliders`.

    **Miscellaneous signature changes**
    When updating the API and moving from previous libraries to Pillow, some miscellaneous changes also happened, meaning some methods signatures may have changed. You should check the new signatures if you used any of the following:
    - `TextClip`: some arguments named have changed and a path to a font file is now needed at object instantiation.
    - `clip.resize` is now `clip.resized`.
    - `clip.crop` is now `clip.cropped`.
    - `clip.rotate` is now `clip.rotated`.
    - Any previous `Clip` method not starting by `with_` now probably starts with it.

    **Why all these changes and updating from v1.0 to v2.0?**
    These changes were introduced to simplify future development and limit confusion by providing a unified environment, which required breaking changes, and so a new major version release was required.

    ---
    The script must be complete and executable Python code.
    """


    def validate_script(self, script_code: str, sandbox_path: str, inputs: dict, outputs: dict) -> Tuple[bool, Optional[str]]:
        """
        Validates the generated MoviePy script within a pre-populated sandbox directory.
        This involves:
        1. Creating dummy videos that match the metadata of the real inputs (if any input video).
        2. Modifying the script's `inputs` to point to these dummies.
        3. Executing the script in the sandbox.
        4. Checking if the output video was created and is readable.
        """
        try:
            # Check for MoviePy-specific imports directly to ensure LLM is following instructions
            # This is a heuristic check, LLM might find other ways, but it guides it.
            if "from moviepy.editor import" in script_code and "concatenate_videoclips" not in script_code and "concatenate_audioclips" not in script_code and "clips_array" not in script_code:
                # Allow 'from moviepy.editor import' ONLY for the specific high-level functions, otherwise it's deprecated.
                return False, "[Validation Error] Script used deprecated 'moviepy.editor' import for core classes. Please use direct sub-module imports."

            ast.parse(script_code)
        except SyntaxError as e:
            return False, f"[SyntaxError] Invalid Python syntax: {e}"

        dummy_files_created = []
        try:
            # 1. Create dummy versions of all video inputs
            dummy_inputs = inputs.copy()

            for key, filename in inputs.items():
                if isinstance(filename, str) and filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    real_path = os.path.join(sandbox_path, filename)
                    # Handle case where file might not exist (e.g., from a failed previous step)
                    if not os.path.exists(real_path):
                        placeholder_meta = {'width': 640, 'height': 480, 'duration': 10, 'frame_rate': 24}
                        _create_dummy_video(real_path, placeholder_meta)
                        logger.warning(f"Real input {filename} not found in sandbox, created a generic placeholder for validation.")

                    metadata = _get_video_metadata(real_path)
                    if not metadata:
                        return False, f"[SandboxError] Could not read metadata from real input file: {filename}"
                    
                    dummy_filename = f"dummy_{filename}"
                    dummy_path = os.path.join(sandbox_path, dummy_filename)
                    _create_dummy_video(dummy_path, metadata)
                    
                    dummy_files_created.append(dummy_path)
                    dummy_inputs[key] = dummy_filename
                elif isinstance(filename, str) and os.path.exists(os.path.join(sandbox_path, filename)):
                    dummy_inputs[key] = filename 

            # 2. Construct and write the test script
            inputs_def = f"inputs = {json.dumps(dummy_inputs)}"
            outputs_def = f"outputs = {json.dumps(outputs)}"
            full_test_script = f"{inputs_def}\n{outputs_def}\n\n{script_code}"

            script_path_in_sandbox = os.path.join(sandbox_path, "test_script.py")
            with open(script_path_in_sandbox, "w") as f: f.write(full_test_script)

            # 3. Execute the script and find the output
            files_before = set(os.listdir(sandbox_path))
            result = subprocess.run(
                [sys.executable, script_path_in_sandbox],
                cwd=sandbox_path, check=True, capture_output=True, text=True,
                timeout=MOVIEPY_TIMEOUT
            )
            files_after = set(os.listdir(sandbox_path))
            
            output_found = False
            for output_key, output_filename in outputs.items():
                if output_filename in (files_after - files_before):
                    output_found = True
                    output_path = os.path.join(sandbox_path, output_filename)
                    # For MoviePy, if it's supposed to create a video, check if it's readable.
                    if output_filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                        if not _is_video_readable(output_path):
                            return False, f"[SandboxError] Script produced a corrupt or unreadable output video file: {output_filename}"
                    # Add checks for other file types here if MoviePy can output them (e.g., GIF)
                    
            if not output_found:
                new_videos = [f for f in (files_after - files_before) if f.lower().endswith(('.mp4', '.mov'))]
                if new_videos and any(val.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')) for val in outputs.values()):
                    output_path = os.path.join(sandbox_path, new_videos[0])
                    if _is_video_readable(output_path):
                        logger.warning(f"Script created an unexpected video file '{new_videos[0]}' but it was valid. Passing.")
                        return True, None
                return False, "[SandboxError] Script ran successfully but did not create any of the expected output files."
                
            return True, None

        except subprocess.TimeoutExpired:
            return False, f"[SandboxError] Script execution timed out."
        except subprocess.CalledProcessError as e:
            error_details = f"Exit Code: {e.returncode}\n"
            if e.stdout: error_details += f"Stdout:\n{e.stdout.strip()}\n"
            if e.stderr: error_details += f"Stderr:\n{e.stderr.strip()}"
            return False, f"[SandboxError] Script failed during execution.\n{error_details}"
        except Exception as e:
            return False, f"[SandboxError] An unexpected error occurred during validation: {e}"
        finally:
            for f in dummy_files_created:
                if os.path.exists(f):
                    os.remove(f)
#!/usr/bin/env python3

import sys
import os

# Add the project root to Python path
sys.path.insert(0, '/home/idrees-mustafa/Dev/editor-MVP/GPT_Editor_MVP')

def test_ffmpeg_import():
    try:
        import ffmpeg
        print("✅ ffmpeg-python imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Failed to import ffmpeg: {e}")
        return False

def test_simple_script():
    # Test the exact script pattern we generate
    script_content = '''
import ffmpeg
import sys
import os

def main():
    if len(sys.argv) != 3:
        print("Usage: python script.py <input_file> <output_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' not found")
        sys.exit(1)
    
    try:
        # Convert to grayscale
        stream = ffmpeg.input(input_file)
        stream = stream.filter('colorchannelmixer', rr=0.3, rg=0.59, rb=0.11, gr=0.3, gg=0.59, gb=0.11, br=0.3, bg=0.59, bb=0.11)
        stream = stream.output(output_file)
        ffmpeg.run(stream, overwrite_output=True)
        print(f"Successfully processed {input_file} -> {output_file}")
    except ffmpeg.Error as e:
        print(f"FFmpeg error: {e}")
        if hasattr(e, 'stderr') and e.stderr:
            print(f"FFmpeg stderr: {e.stderr.decode('utf-8')}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
'''
    
    script_path = '/tmp/test_ffmpeg_script.py'
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    # Test with actual files
    input_file = '/home/idrees-mustafa/Dev/editor-MVP/GPT_Editor_MVP/sessions/ba45522c-5c3f-4835-b7ce-fd52755f3706/assets/sunset_image/image.png'
    output_file = '/tmp/test_output.png'
    
    if not os.path.exists(input_file):
        print(f"❌ Input file doesn't exist: {input_file}")
        return False
    
    import subprocess
    try:
        result = subprocess.run([
            'python', script_path, input_file, output_file
        ], capture_output=True, text=True, timeout=30)
        
        print(f"Script exit code: {result.returncode}")
        if result.stdout:
            print(f"Stdout: {result.stdout}")
        if result.stderr:
            print(f"Stderr: {result.stderr}")
            
        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            print(f"✅ Output file created: {output_file} ({size} bytes)")
            os.remove(output_file)  # cleanup
            return True
        else:
            print("❌ Output file not created")
            return False
            
    except Exception as e:
        print(f"❌ Script execution failed: {e}")
        return False
    finally:
        if os.path.exists(script_path):
            os.remove(script_path)

if __name__ == "__main__":
    print("🧪 Testing FFmpeg functionality...")
    print()
    
    print("1. Testing ffmpeg-python import...")
    import_ok = test_ffmpeg_import()
    print()
    
    if import_ok:
        print("2. Testing simple script execution...")
        script_ok = test_simple_script()
        print()
        
        if script_ok:
            print("✅ All tests passed! FFmpeg functionality works.")
        else:
            print("❌ Script execution failed.")
    else:
        print("❌ Cannot proceed without ffmpeg-python import.")

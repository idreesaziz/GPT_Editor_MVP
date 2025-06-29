import requests
import sys
import os

API_URL = "http://127.0.0.1:8000"

def test_flow(video_path: str, prompt: str):
    # 1. Upload video
    with open(video_path, "rb") as f:
        response = requests.post(f"{API_URL}/upload", files={"file": f})
    
    if response.status_code != 200:
        print(f"Error uploading video: {response.text}")
        return

    session_id = response.json()["session_id"]
    print(f"Video uploaded. Session ID: {session_id}")

    # 2. Edit video
    edit_payload = {"session_id": session_id, "prompt": prompt}
    response = requests.post(f"{API_URL}/edit", json=edit_payload)

    if response.status_code != 200:
        print(f"Error editing video: {response.text}")
        return

    result = response.json()
    print(f"Edit successful. Output URL: {result['output_url']}")
    print(f"Script used: {result['script_used']}")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python test_flow.py <video_path> \"<prompt>\"")
        sys.exit(1)
    
    video_path = sys.argv[1]
    prompt = sys.argv[2]

    if not os.path.exists(video_path):
        print(f"Error: Video file not found at {video_path}")
        sys.exit(1)

    test_flow(video_path, prompt)

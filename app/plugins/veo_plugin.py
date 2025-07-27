# app/plugins/veo_plugin.py

import logging
import os
import time
import json
import requests
import shutil
from typing import Dict, List

from app.plugins.base import ToolPlugin

# --- Configuration ---
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
VEO_OUTPUT_GCS_BUCKET = os.getenv("VEO_OUTPUT_GCS_BUCKET")

# --- DUMMY MODE SWITCH ---
VEO_DUMMY_MODE = os.getenv("VEO_DUMMY_MODE", "false").lower() == "true"
DUMMY_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "dummy_video.mp4")

VEO_MODEL_ID = "veo-2.0-generate-001"
JOB_TIMEOUT_SECONDS = 900 # 15 minutes

# --- Custom Exception ---
class VeoGenerationError(Exception):
    """Custom exception for errors during Veo video generation."""
    pass

# --- Plugin Definition ---
class VeoVideoGenerator(ToolPlugin):
    """
    A plugin that generates a single, high-quality video file from a text prompt
    using Google's Veo model on Vertex AI. Includes a dummy mode for cost-free testing.
    """

    def __init__(self):
        super().__init__()
        if not VEO_DUMMY_MODE and (not VERTEX_PROJECT_ID or not VEO_OUTPUT_GCS_BUCKET):
            raise ValueError("In non-dummy mode, VERTEX_PROJECT_ID and VEO_OUTPUT_GCS_BUCKET must be set.")

    @property
    def name(self) -> str:
        return "Veo Video Generator"

    @property
    def description(self) -> str:
        return (
            "Generates photorealistic or stylized video clips (e.g., MP4) from a descriptive text prompt. "
            "Use this for creating cinematic shots, real-world scenes (a dog running on a beach), drone footage, "
            "or abstract visual concepts (a psychedelic tunnel). This tool currently only accepts text as input."
        )

    def _execute_dummy_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        """Bypasses the API and provides a placeholder video for testing."""
        run_logger.info("VEO PLUGIN: --- DUMMY MODE ENABLED ---")
        
        if not os.path.exists(DUMMY_VIDEO_PATH):
            error_msg = f"Dummy video file not found at {DUMMY_VIDEO_PATH}. Please create it."
            run_logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        prompt = task_details["task"]
        output_filename = task_details.get("output_filename", "video.mp4")
        final_output_path = os.path.join(asset_unit_path, output_filename)

        run_logger.info(f"VEO PLUGIN (DUMMY): Copying dummy video to {final_output_path}")
        shutil.copy(DUMMY_VIDEO_PATH, final_output_path)
        time.sleep(0.5)

        plugin_data = {
            "source_prompt": prompt,
            "gcs_uri": "dummy/path/to/video.mp4",
            "operation_name": "dummy-operation-123",
            "is_dummy": True
        }
        self._create_metadata_file(task_details, asset_unit_path, [output_filename], plugin_data)
        
        run_logger.info(f"VEO PLUGIN (DUMMY): Successfully generated dummy asset unit '{task_details['unit_id']}'.")
        return [output_filename]

    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        if VEO_DUMMY_MODE:
            return self._execute_dummy_task(task_details, asset_unit_path, run_logger)

        prompt = task_details["task"]
        unit_id = task_details["unit_id"]
        output_filename = task_details.get("output_filename", "video.mp4")

        run_logger.info(f"VEO PLUGIN: Starting task for unit '{unit_id}'.")
        run_logger.info(f"VEO PLUGIN: Prompt: '{prompt[:100]}...'")

        try:
            from google.auth import default
            from google.auth.transport.requests import Request
            
            run_logger.info("VEO PLUGIN: Attempting to authenticate with Google Cloud...")
            
            scopes = ['https://www.googleapis.com/auth/cloud-platform']
            credentials, project = default(scopes=scopes)
            
            if not credentials.valid:
                run_logger.info("VEO PLUGIN: Refreshing credentials...")
                credentials.refresh(Request())
            
            access_token = credentials.token
            if not access_token:
                raise VeoGenerationError("No access token available after authentication")
            
            run_logger.info("VEO PLUGIN: Authentication successful")
            
            gcs_output_uri = f"gs://{VEO_OUTPUT_GCS_BUCKET}/{unit_id}/"
            
            request_payload = {
                "instances": [{"prompt": prompt}],
                "parameters": {
                    "storageUri": gcs_output_uri,
                    "sampleCount": 1,
                    "durationSeconds": 8,
                    "aspectRatio": "16:9",
                    "enhancePrompt": True
                }
            }
            
            predict_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT_ID}/locations/{VERTEX_LOCATION}/publishers/google/models/{VEO_MODEL_ID}:predictLongRunning"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
            
            run_logger.info("VEO PLUGIN: Submitting video generation request to Vertex AI...")
            response = requests.post(predict_url, headers=headers, json=request_payload)
            response.raise_for_status()
            
            operation_name = response.json().get("name")
            if not operation_name:
                raise VeoGenerationError("No operation name returned from Veo API")
            
            run_logger.info(f"VEO PLUGIN: Operation started: {operation_name}")
            
            fetch_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT_ID}/locations/{VERTEX_LOCATION}/publishers/google/models/{VEO_MODEL_ID}:fetchPredictOperation"
            fetch_payload = {"operationName": operation_name}
            
            run_logger.info("VEO PLUGIN: Polling for completion...")
            start_time = time.time()
            
            while time.time() - start_time < JOB_TIMEOUT_SECONDS:
                poll_response = requests.post(fetch_url, headers=headers, json=fetch_payload)
                poll_response.raise_for_status()
                poll_data = poll_response.json()
                
                if poll_data.get("done", False):
                    run_logger.info("VEO PLUGIN: Video generation completed successfully.")
                    response_data = poll_data.get("response", {})
                    videos = response_data.get("videos", [])
                    
                    if not videos:
                        raise VeoGenerationError("No videos found in the completed operation response")
                    
                    gcs_uri = videos[0].get("gcsUri")
                    if not gcs_uri:
                        raise VeoGenerationError("No GCS URI found in video response")
                    
                    run_logger.info(f"VEO PLUGIN: Video generated at GCS URI: {gcs_uri}")
                    
                    final_output_path = os.path.join(asset_unit_path, output_filename)
                    self._download_gcs_file(gcs_uri, final_output_path, run_logger)
                    
                    plugin_data = {"source_prompt": prompt, "gcs_uri": gcs_uri, "is_dummy": False}
                    self._create_metadata_file(task_details, asset_unit_path, [output_filename], plugin_data)
                    
                    run_logger.info(f"VEO PLUGIN: Successfully generated asset unit '{unit_id}'.")
                    return [output_filename]
                
                error = poll_data.get("error")
                if error:
                    raise VeoGenerationError(f"Veo operation failed: {error.get('message', 'Unknown error')}")
                
                run_logger.info("VEO PLUGIN: Operation still running, waiting...")
                time.sleep(20)
            
            raise VeoGenerationError(f"Video generation timed out after {JOB_TIMEOUT_SECONDS} seconds")

        except requests.exceptions.HTTPError as e:
            run_logger.error(f"VEO PLUGIN: HTTP Error: {e.response.status_code} - {e.response.text}", exc_info=True)
            raise VeoGenerationError(f"API request failed: {e.response.text}") from e
        except Exception as e:
            run_logger.error(f"VEO PLUGIN: Error during Veo generation for unit '{unit_id}': {e}", exc_info=True)
            raise VeoGenerationError(f"Failed to generate video with Veo: {e}") from e

    def _download_gcs_file(self, gcs_uri: str, local_path: str, run_logger: logging.Logger):
        """Download a file from Google Cloud Storage to local path."""
        try:
            from google.cloud import storage
            
            if not gcs_uri.startswith("gs://"):
                raise ValueError(f"Invalid GCS URI format: {gcs_uri}")
            
            gcs_path = gcs_uri[5:]
            bucket_name, blob_name = gcs_path.split("/", 1)
            
            run_logger.info(f"VEO PLUGIN: Downloading {gcs_uri} to {local_path}")
            
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            blob.download_to_filename(local_path)
            
            run_logger.info(f"VEO PLUGIN: Successfully downloaded video to {local_path}")
            
        except Exception as e:
            run_logger.error(f"VEO PLUGIN: Failed to download from GCS: {e}", exc_info=True)
            raise VeoGenerationError(f"Failed to download generated video: {e}") from e
            
    # RESTORING your working _create_metadata_file method
    def _create_metadata_file(self, task_details: Dict, asset_unit_path: str, output_files: List[str], plugin_data: Dict):
        """Create metadata file for the generated assets."""
        # This was your custom metadata structure that was working.
        metadata = {
            "plugin": "veo",
            "task_details": task_details,
            "output_files": output_files,
            "plugin_data": plugin_data,
            "generated_at": time.time(),
            "source_prompt": task_details.get('task') # Ensuring source_prompt is present
        }
        
        metadata_path = os.path.join(asset_unit_path, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
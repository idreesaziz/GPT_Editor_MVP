# app/plugins/veo_plugin.py

import logging
import os
import time
import json
import requests
from typing import Dict, List

from app.plugins.base import ToolPlugin

# --- Configuration ---
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
VEO_OUTPUT_GCS_BUCKET = os.getenv("VEO_OUTPUT_GCS_BUCKET")

# As requested, we will use the GA model
VEO_MODEL_ID = "veo-2.0-generate-001"

# Polling configuration for the long-running job
JOB_TIMEOUT_SECONDS = 900 # 15 minutes

# --- Custom Exception ---
class VeoGenerationError(Exception):
    """Custom exception for errors during Veo video generation."""
    pass

# --- Plugin Definition ---
class VeoVideoGenerator(ToolPlugin):
    """
    A plugin that generates a single, high-quality video file from a text prompt
    using Google's Veo model on Vertex AI. This is a synchronous, blocking operation.
    """

    def __init__(self):
        super().__init__()
        if not VERTEX_PROJECT_ID or not VEO_OUTPUT_GCS_BUCKET:
            raise ValueError("VERTEX_PROJECT_ID and VEO_OUTPUT_GCS_BUCKET environment variables must be set.")
        
        # No SDK initialization needed - we'll use REST API with authentication

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

    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        prompt = task_details["task"]
        unit_id = task_details["unit_id"]
        output_filename = task_details.get("output_filename", "video.mp4")

        run_logger.info(f"VEO PLUGIN: Starting task for unit '{unit_id}'.")
        run_logger.info(f"VEO PLUGIN: Prompt: '{prompt[:100]}...'")

        try:
            # Get access token using Application Default Credentials
            from google.auth import default
            from google.auth.transport.requests import Request
            import google.auth
            
            run_logger.info("VEO PLUGIN: Attempting to authenticate with Google Cloud...")
            
            # Try to get credentials with explicit scopes
            try:
                scopes = ['https://www.googleapis.com/auth/cloud-platform']
                credentials, project = default(scopes=scopes)
                run_logger.info(f"VEO PLUGIN: Successfully obtained credentials for project: {project}")
            except Exception as scope_error:
                run_logger.warning(f"VEO PLUGIN: Failed to get scoped credentials: {scope_error}")
                # Fallback to default credentials without scopes
                credentials, project = default()
                run_logger.info(f"VEO PLUGIN: Using default credentials for project: {project}")
            
            # Refresh credentials if needed
            if not credentials.valid:
                run_logger.info("VEO PLUGIN: Refreshing credentials...")
                try:
                    credentials.refresh(Request())
                    run_logger.info("VEO PLUGIN: Credentials refreshed successfully")
                except Exception as auth_error:
                    run_logger.error(f"VEO PLUGIN: Authentication failed: {auth_error}")
                    raise VeoGenerationError("Failed to authenticate with Google Cloud. Please check your service account configuration.") from auth_error
            
            access_token = credentials.token
            
            if not access_token:
                raise VeoGenerationError("No access token available after authentication")
                
            run_logger.info("VEO PLUGIN: Authentication successful")
            
            # Prepare the request payload for Veo API
            gcs_output_uri = f"gs://{VEO_OUTPUT_GCS_BUCKET}/{unit_id}/"
            
            request_payload = {
                "instances": [
                    {
                        "prompt": prompt
                    }
                ],
                "parameters": {
                    "storageUri": gcs_output_uri,
                    "sampleCount": 1,
                    "durationSeconds": 8,  # Veo 2.0 supports 5-8 seconds, using 8 as default
                    "aspectRatio": "16:9",
                    "enhancePrompt": True
                }
            }
            
            # Submit the long-running prediction request
            predict_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT_ID}/locations/{VERTEX_LOCATION}/publishers/google/models/{VEO_MODEL_ID}:predictLongRunning"
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=utf-8"
            }
            
            run_logger.info("VEO PLUGIN: Submitting video generation request to Vertex AI...")
            response = requests.post(predict_url, headers=headers, json=request_payload)
            response.raise_for_status()
            
            operation_data = response.json()
            operation_name = operation_data.get("name")
            
            if not operation_name:
                raise VeoGenerationError("No operation name returned from Veo API")
            
            run_logger.info(f"VEO PLUGIN: Operation started: {operation_name}")
            
            # Poll for completion
            operation_id = operation_name.split("/")[-1]
            fetch_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT_ID}/locations/{VERTEX_LOCATION}/publishers/google/models/{VEO_MODEL_ID}:fetchPredictOperation"
            
            fetch_payload = {
                "operationName": operation_name
            }
            
            run_logger.info("VEO PLUGIN: Polling for completion...")
            start_time = time.time()
            
            while time.time() - start_time < JOB_TIMEOUT_SECONDS:
                poll_response = requests.post(fetch_url, headers=headers, json=fetch_payload)
                poll_response.raise_for_status()
                
                poll_data = poll_response.json()
                
                if poll_data.get("done", False):
                    run_logger.info("VEO PLUGIN: Video generation completed successfully.")
                    
                    # Extract the video GCS URI from the response
                    response_data = poll_data.get("response", {})
                    videos = response_data.get("videos", [])
                    
                    if not videos:
                        # Try alternative response structure
                        predictions = response_data.get("predictions", [])
                        if predictions and isinstance(predictions[0], dict):
                            # Check if predictions contain video info
                            for prediction in predictions:
                                if "gcsUri" in prediction:
                                    videos = [prediction]
                                    break
                                # Check nested structure
                                elif "videos" in prediction:
                                    videos = prediction["videos"]
                                    break
                    
                    if not videos:
                        run_logger.error(f"VEO PLUGIN: No videos found in response")
                        raise VeoGenerationError("No videos found in the completed operation response")
                    
                    # Get the first generated video
                    video_info = videos[0]
                    gcs_uri = video_info.get("gcsUri")
                    
                    if not gcs_uri:
                        run_logger.error(f"VEO PLUGIN: No GCS URI found in video response")
                        raise VeoGenerationError("No GCS URI found in video response")
                    
                    run_logger.info(f"VEO PLUGIN: Video generated at GCS URI: {gcs_uri}")
                    
                    # Download the generated video from GCS
                    final_output_path = os.path.join(asset_unit_path, output_filename)
                    self._download_gcs_file(gcs_uri, final_output_path, run_logger)
                    
                    # Create metadata and return
                    plugin_data = {
                        "source_prompt": prompt,
                        "gcs_uri": gcs_uri,
                        "operation_name": operation_name,
                        "duration_seconds": 5,
                        "aspect_ratio": "16:9"
                    }
                    
                    # Create metadata with the prompt in plugin_data for test compatibility
                    metadata = {
                        "plugin": "veo",
                        "task_details": task_details,
                        "output_files": [output_filename],
                        "plugin_data": plugin_data,
                        "generated_at": time.time(),
                        "source_prompt": prompt  # Add this for test compatibility
                    }
                    
                    metadata_path = os.path.join(asset_unit_path, "metadata.json")
                    with open(metadata_path, "w") as f:
                        json.dump(metadata, f, indent=2)
                    
                    run_logger.info(f"VEO PLUGIN: Successfully generated asset unit '{unit_id}'.")
                    return [output_filename]
                
                # Check for errors in the operation
                error = poll_data.get("error")
                if error:
                    error_message = error.get("message", "Unknown error occurred")
                    raise VeoGenerationError(f"Veo operation failed: {error_message}")
                
                # Wait before polling again
                run_logger.info("VEO PLUGIN: Operation still running, waiting...")
                time.sleep(10)  # Poll every 10 seconds
            
            # If we reach here, the operation timed out
            raise VeoGenerationError(f"Video generation timed out after {JOB_TIMEOUT_SECONDS} seconds")

        except requests.exceptions.RequestException as e:
            run_logger.error(f"VEO PLUGIN: HTTP request failed: {e}", exc_info=True)
            raise VeoGenerationError(f"Failed to communicate with Veo API: {e}") from e
        except Exception as e:
            run_logger.error(f"VEO PLUGIN: Error during Veo generation for unit '{unit_id}': {e}", exc_info=True)
            raise VeoGenerationError(f"Failed to generate video with Veo: {e}") from e

    def _download_gcs_file(self, gcs_uri: str, local_path: str, run_logger: logging.Logger):
        """Download a file from Google Cloud Storage to local path."""
        try:
            # Parse the GCS URI (format: gs://bucket/path/to/file)
            if not gcs_uri.startswith("gs://"):
                raise ValueError(f"Invalid GCS URI format: {gcs_uri}")
            
            # Remove gs:// prefix and split bucket and blob path
            gcs_path = gcs_uri[5:]  # Remove 'gs://'
            bucket_name, blob_name = gcs_path.split("/", 1)
            
            run_logger.info(f"VEO PLUGIN: Downloading {gcs_uri} to {local_path}")
            
            # Initialize GCS client and download
            from google.cloud import storage
            storage_client = storage.Client()
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            
            # Ensure the local directory exists
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            
            # Download the file
            blob.download_to_filename(local_path)
            
            run_logger.info(f"VEO PLUGIN: Successfully downloaded video to {local_path}")
            
        except Exception as e:
            run_logger.error(f"VEO PLUGIN: Failed to download from GCS: {e}", exc_info=True)
            raise VeoGenerationError(f"Failed to download generated video: {e}") from e

    def _create_metadata_file(self, task_details: Dict, asset_unit_path: str, output_files: List[str], plugin_data: Dict):
        """Create metadata file for the generated assets."""
        metadata = {
            "plugin": "veo",
            "task_details": task_details,
            "output_files": output_files,
            "plugin_data": plugin_data,
            "generated_at": time.time()
        }
        
        metadata_path = os.path.join(asset_unit_path, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
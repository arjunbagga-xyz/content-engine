import os
import zipfile
import logging
import requests
import time
import asyncio
from pathlib import Path
import yaml
from src.core.config import config

logger = logging.getLogger("content_engine.lora_trainer")

class LoRATrainer:
    @staticmethod
    def zip_dataset(image_dir: Path, output_zip_path: Path) -> Path:
        """Zips all images in a directory to prepare for LoRA training upload."""
        logger.info(f"Zipping training dataset in {image_dir}...")
        image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
        
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file in image_dir.iterdir():
                if file.is_file() and file.suffix.lower() in image_extensions:
                    # Also grab optional matching caption text files if they exist
                    zipf.write(file, file.name)
                    caption_file = file.with_suffix(".txt")
                    if caption_file.exists():
                        zipf.write(caption_file, caption_file.name)
                        
        logger.info(f"Dataset zipped successfully to {output_zip_path} (Size: {output_zip_path.stat().st_size / 1024:.1f} KB)")
        return output_zip_path

    @staticmethod
    async def train_fal_lora(
        character_id: str,
        dataset_zip_path: Path,
        trigger_word: str,
        is_video: bool = False,
        steps: int = 1000
    ) -> str:
        """
        Trains an image (FLUX.1) or video (HunyuanVideo) LoRA model on Fal.ai.
        Bypasses SDK dependencies by using direct requests on standard fal API queue.
        
        Returns:
            URL to the trained safetensors file.
        """
        if not config.FAL_API_KEY or config.FAL_API_KEY == "your_key_here":
            raise ValueError("FAL_API_KEY is not configured in .env")

        headers = {
            "Authorization": f"Key {config.FAL_API_KEY}",
            "Content-Type": "application/json"
        }

        # Step 1: Upload ZIP file to Fal's temporary storage
        logger.info("Uploading dataset to Fal temporary storage...")
        upload_url = "https://files.fal.run/upload"
        
        with open(dataset_zip_path, "rb") as f:
            upload_headers = {"Authorization": f"Key {config.FAL_API_KEY}"}
            files = {"file": (dataset_zip_path.name, f, "application/zip")}
            upload_res = requests.post(upload_url, headers=upload_headers, files=files)
            
        if upload_res.status_code != 200:
            raise RuntimeError(f"Failed to upload dataset: {upload_res.text}")
            
        dataset_url = upload_res.json().get("url")
        logger.info(f"Dataset successfully uploaded. URL: {dataset_url}")

        # Step 2: Trigger the training queue
        endpoint = "fal-ai/hunyuan-video-lora-training" if is_video else "fal-ai/flux-lora-fast-training"
        queue_url = f"https://queue.fal.run/{endpoint}"
        
        payload = {
            "images_data_url": dataset_url,
            "trigger_word": trigger_word,
            "steps" if is_video else "num_steps": steps,
        }
        
        if not is_video:
            payload["is_style"] = False
            payload["preprocessed_data"] = False
        else:
            payload["learning_rate"] = 0.0001
            payload["do_caption"] = True

        logger.info(f"Submitting { 'HunyuanVideo' if is_video else 'FLUX.1' } LoRA training job...")
        response = requests.post(queue_url, json=payload, headers=headers)
        if response.status_code not in (200, 202):
            raise RuntimeError(f"Failed to submit training job: {response.text}")
            
        job_data = response.json()
        request_id = job_data.get("request_id")
        logger.info(f"Job successfully queued! Request ID: {request_id}")

        # Step 3: Poll the job status until success
        status_url = f"https://queue.fal.run/{endpoint}/requests/{request_id}"
        max_polls = 120 # ~20 minutes maximum
        
        for i in range(max_polls):
            await asyncio.sleep(10)
            status_res = requests.get(status_url, headers=headers)
            if status_res.status_code == 200:
                result = status_res.json()
                if "diff" in result:
                    safetensors_url = result["diff"].get("url")
                    logger.info(f"LoRA Training Completed! SafeTensors URL: {safetensors_url}")
                    LoRATrainer._update_character_config(character_id, safetensors_url)
                    return safetensors_url
                elif "images" in result: 
                    safetensors_url = result.get("weights", {}).get("url") or result.get("url")
                    if safetensors_url:
                        LoRATrainer._update_character_config(character_id, safetensors_url)
                        return safetensors_url
            elif status_res.status_code == 202:
                logger.info(f"Training in progress (Poll {i+1}/{max_polls})...")
            else:
                raise RuntimeError(f"Error checking job status: {status_res.text}")
                
        raise TimeoutError("LoRA training took too long and timed out.")

    @staticmethod
    def _update_character_config(character_id: str, lora_weights_url: str):
        """Updates characters.yaml file with the newly trained LoRA url."""
        config_path = Path("config/characters.yaml")
        if not config_path.exists():
            logger.warning("characters.yaml not found, skipping config update.")
            return

        with open(config_path, "r") as f:
            chars = yaml.safe_load(f) or {}

        if character_id in chars:
            if "lora_config" not in chars[character_id]:
                chars[character_id]["lora_config"] = {}
            chars[character_id]["lora_config"]["model_id"] = lora_weights_url
            chars[character_id]["lora_config"]["weights_path"] = lora_weights_url
            
            with open(config_path, "w") as f:
                yaml.safe_dump(chars, f, default_flow_style=False)
            logger.info(f"Updated '{character_id}' in characters.yaml with new LoRA weights URL.")

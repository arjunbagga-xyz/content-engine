import os
import uuid
import time
import logging
import asyncio
import requests
import urllib.parse
from pathlib import Path
from src.core.config import config

logger = logging.getLogger("content_engine.consistency")

class ConsistencyPromptBuilder:
    @staticmethod
    def build_prompt(character_config: dict, base_prompt: str) -> dict:
        """
        Builds a consistent prompt by combining character visual identity anchors and the trigger word.
        Format follows: [trigger_word], [visual_anchors], [base_prompt], [high_quality_tags]
        """
        visual_id = character_config.get("visual_identity", {})
        if not visual_id:
            return {"prompt": base_prompt, "negative_prompt": ""}

        trigger = visual_id.get("trigger_word", "")
        hair = visual_id.get("hair", "")
        eyes = visual_id.get("eyes", "")
        skin = visual_id.get("skin", "")
        build = visual_id.get("build", "")
        style = visual_id.get("style", "")
        distinguishing = visual_id.get("distinguishing", "")
        age = visual_id.get("age_look", "")
        ethnicity = visual_id.get("ethnicity_look", "")
        # A locked, prose identity descriptor keeps the SAME person across every
        # generation even on free/no-LoRA image models that don't know the trigger word.
        seed_descriptor = visual_id.get("seed_descriptor", "")

        # Format details
        details = []
        if trigger:
            details.append(trigger)
        if hair:
            details.append(f"{hair} hair")
        if eyes:
            details.append(f"{eyes} eyes")
        if skin:
            details.append(f"{skin} skin")
        if build:
            details.append(f"{build} build")
        if age:
            details.append(f"{age}")
        if ethnicity:
            details.append(f"{ethnicity}")
        if style:
            details.append(style)
        if distinguishing:
            details.append(distinguishing)

        features_str = ", ".join(details)
        if seed_descriptor:
            features_str = f"{seed_descriptor}, {features_str}"

        # Photorealistic photography prompt — avoids the illustration/AI-slop look.
        # The locked descriptor + fixed seed gives the character a consistent face
        # across posts even without a trained LoRA.
        full_prompt = (
            f"photorealistic photograph of {features_str}, {base_prompt}, "
            f"shot on 85mm portrait lens, shallow depth of field, soft natural window light, "
            f"natural skin texture with visible pores and fine imperfections, "
            f"real human, authentic unposed candid moment, film grain, true-to-life color grading, "
            f"high detail, sharp focus on face"
        )
        
        # Comprehensive anti-slop negative prompt to avoid plastic/CGI look
        negative_prompt = visual_id.get(
            "negative_prompt", 
            "cgi, 3d, render, drawing, painting, cartoon, anime, illustration, lowres, bad anatomy, bad hands, missing fingers, extra digits, fewer digits, cropped, worst quality, low quality, duplicate, deformed, blurry, plastic skin, bad eyes, deformed iris, double eyebrows, glowing eyes"
        )

        return {
            "prompt": full_prompt,
            "negative_prompt": negative_prompt
        }

class LoRAInferenceRouter:
    @staticmethod
    async def generate_image(character_config: dict, base_prompt: str, output_path: str) -> str:
        """
        Generates a consistent character image using a multi-provider fallback and recursive Visual QA.
        """
        from src.generation.qa import VisualQA
        from src.core.config import config
        
        settings = config.load_settings()
        max_qa_retries = settings.get("max_retries", 3)
        
        prompt_adjustments = ""
        current_base_prompt = base_prompt
        
        for attempt in range(1, max_qa_retries + 1):
            logger.info(f"Visual Generation attempt {attempt}/{max_qa_retries}...")
            
            # Incorporate adjustments if any
            if prompt_adjustments:
                logger.info(f"Applying Visual QA adjustments: {prompt_adjustments}")
                adjusted_base_prompt = f"{current_base_prompt}. Note: {prompt_adjustments}"
            else:
                adjusted_base_prompt = current_base_prompt
                
            prompt_data = ConsistencyPromptBuilder.build_prompt(character_config, adjusted_base_prompt)
            prompt = prompt_data["prompt"]
            negative_prompt = prompt_data["negative_prompt"]
            
            lora_config = character_config.get("lora_config", {})
            image_generated = False
            
            # 1st Priority: Tensor.Art
            if config.TENSOR_ART_API_KEY and config.TENSOR_ART_API_KEY != "your_key_here":
                try:
                    logger.info("Attempting image generation via Tensor.Art...")
                    path = await LoRAInferenceRouter._generate_tensor_art(prompt, negative_prompt, lora_config, output_path)
                    if path:
                        image_generated = True
                except Exception as e:
                    logger.warning(f"Tensor.Art generation failed: {str(e)}. Falling back.")

            # 2nd Priority: Fal.ai
            if not image_generated and config.FAL_API_KEY and config.FAL_API_KEY != "your_key_here":
                try:
                    logger.info("Attempting image generation via Fal.ai...")
                    path = await LoRAInferenceRouter._generate_fal_ai(prompt, negative_prompt, lora_config, output_path)
                    if path:
                        image_generated = True
                except Exception as e:
                    logger.warning(f"Fal.ai generation failed: {str(e)}. Falling back.")

            # 3rd Priority: CivitAI
            if not image_generated and config.CIVITAI_API_KEY and config.CIVITAI_API_KEY != "your_key_here":
                try:
                    logger.info("Attempting image generation via CivitAI...")
                    path = await LoRAInferenceRouter._generate_civitai(prompt, negative_prompt, lora_config, output_path)
                    if path:
                        image_generated = True
                except Exception as e:
                    logger.warning(f"CivitAI generation failed: {str(e)}. Falling back.")

            # 4th Priority: Pollinations.ai (Free fallback)
            if not image_generated:
                logger.info("Falling back to Pollinations.ai (free prompt-only FLUX)...")
                from src.generation.image import ImageGenerator
                try:
                    # Stable per-character seed => the SAME face every post (consistency).
                    stable_seed = character_config.get("visual_identity", {}).get("seed", 12345)
                    path = await ImageGenerator.generate_ai_character_image(
                        prompt, output_path, seed=stable_seed, negative_prompt=negative_prompt
                    )
                    if path:
                        image_generated = True
                except Exception as e:
                    logger.error(f"Pollinations generation failed: {str(e)}")
                    
            if not image_generated:
                logger.error("Failed to generate image from any provider on this attempt.")
                # FALLBACK TO PEXELS STOCK PHOTO
                try:
                    logger.info("Attempting Pexels stock photo fallback...")
                    from src.generation.image import ImageGenerator
                    keywords = character_config.get("visual_keywords", "cozy PC setup")
                    if isinstance(keywords, list):
                        keywords = ", ".join(keywords)
                    elif not keywords:
                        keywords = "cozy room"
                    # Clean up trailing comma/whitespace
                    keywords = keywords.strip().rstrip(",")
                    path = await ImageGenerator.fetch_pexels_stock_photo(keywords, output_path)
                    if path and os.path.exists(output_path):
                        logger.info("Successfully fetched Pexels stock photo as fallback!")
                        return output_path
                except Exception as ex:
                    logger.error(f"Pexels fallback failed: {str(ex)}")
                continue
                
            # Run Visual QA check on the generated image!
            passed, score, reasons, adjustments = await VisualQA.assess_image(output_path, character_config, adjusted_base_prompt)
            if passed:
                logger.info(f"Visual QA PASSED on attempt {attempt} (score={score})!")
                return output_path
            else:
                logger.warning(f"Visual QA FAILED on attempt {attempt} (score={score}). Reason: {reasons}")
                prompt_adjustments = adjustments
                
        logger.warning(f"Visual QA failed after {max_qa_retries} attempts. Checking if output file exists...")
        if not os.path.exists(output_path):
            logger.error("No image generated and no fallback file exists. Creating solid color fallback image.")
            from PIL import Image
            img = Image.new("RGB", (1080, 1080), "#121212")
            img.save(output_path)
        return output_path

    @staticmethod
    async def _generate_tensor_art(prompt: str, negative_prompt: str, lora_config: dict, output_path: str) -> str:
        """Calls Tensor.Art REST API to perform LoRA SDXL inference."""
        model_id = lora_config.get("model_id")
        if not model_id:
            logger.warning("Tensor.Art requires model_id in lora_config. Skipping.")
            return None

        url = "https://api.tensor.art/v1/jobs"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.TENSOR_ART_API_KEY}"
        }
        
        request_id = str(uuid.uuid4())
        payload = {
            "request_id": request_id,
            "stages": [
                {
                    "type": "INPUT_TEXT",
                    "text": prompt
                },
                {
                    "type": "GENERATE",
                    "params": {
                        "prompt": prompt,
                        "negativePrompt": negative_prompt,
                        "width": 1024,
                        "height": 1024,
                        "samplerName": "Euler a",
                        "steps": 25,
                        "cfgScale": 7,
                        "lora": [
                            {
                                "loraModelId": model_id,
                                "weight": lora_config.get("strength", 0.75)
                            }
                        ]
                    }
                }
            ]
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            logger.warning(f"Tensor.Art job submission failed: {response.text}")
            return None

        job_data = response.json()
        job_id = job_data.get("job", {}).get("id") or job_data.get("id")
        if not job_id:
            logger.warning("Tensor.Art did not return a valid job ID.")
            return None

        # Poll job status
        poll_url = f"https://api.tensor.art/v1/jobs/{job_id}"
        max_retries = 30
        for _ in range(max_retries):
            await asyncio.sleep(5)
            poll_resp = requests.get(poll_url, headers=headers)
            if poll_resp.status_code != 200:
                continue
            
            poll_data = poll_resp.json()
            status = poll_data.get("job", {}).get("status") or poll_data.get("status")
            if status == "SUCCESS":
                image_url = poll_data.get("job", {}).get("successInfo", {}).get("images", [{}])[0].get("url")
                if image_url:
                    img_resp = requests.get(image_url)
                    with open(output_path, "wb") as f:
                        f.write(img_resp.content)
                    logger.info(f"Tensor.Art image generated successfully and saved to {output_path}")
                    return output_path
                break
            elif status == "FAILED":
                logger.warning(f"Tensor.Art job failed: {poll_data}")
                break

        return None

    @staticmethod
    async def _generate_fal_ai(prompt: str, negative_prompt: str, lora_config: dict, output_path: str) -> str:
        """Calls FAL.ai SDXL/Flux LoRA REST API to generate consistent image."""
        model_id = lora_config.get("model_id") or lora_config.get("weights_path")
        # FAL can load standard safetensors by URL or reference weights
        url = "https://queue.fal.run/fal-ai/flux-lora"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Key {config.FAL_API_KEY}"
        }
        
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "image_size": "square_hd",
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "loras": [
                {
                    "path": model_id if model_id.startswith("http") else f"https://civitai.com/api/download/models/{model_id}" if model_id.isdigit() else model_id,
                    "scale": lora_config.get("strength", 0.75)
                }
            ]
        }

        # FAL uses queue-based endpoints
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code not in (200, 202):
            logger.warning(f"FAL.ai queue submission failed: {response.text}")
            return None

        result = response.json()
        request_id = result.get("request_id")
        if not request_id:
            # Maybe it completed synchronously
            image_url = result.get("images", [{}])[0].get("url")
            if image_url:
                return LoRAInferenceRouter._download_image(image_url, output_path)
            return None

        # Poll FAL queue
        status_url = f"https://queue.fal.run/fal-ai/flux-lora/requests/{request_id}"
        for _ in range(20):
            await asyncio.sleep(3)
            status_resp = requests.get(status_url, headers=headers)
            if status_resp.status_code == 200:
                res_data = status_resp.json()
                if "images" in res_data:
                    image_url = res_data["images"][0]["url"]
                    return LoRAInferenceRouter._download_image(image_url, output_path)
            elif status_resp.status_code != 202:
                break
                
        return None

    @staticmethod
    async def _generate_civitai(prompt: str, negative_prompt: str, lora_config: dict, output_path: str) -> str:
        """Calls CivitAI Orchestrator API for LoRA SDXL inference."""
        model_id = lora_config.get("model_id")
        if not model_id:
            logger.warning("CivitAI requires model_id in lora_config. Skipping.")
            return None

        url = "https://orchestrator.civitai.com/v1/jobs"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.CIVITAI_API_KEY}"
        }

        payload = {
            "job": {
                "type": "text-to-image",
                "params": {
                    "prompt": prompt,
                    "negativePrompt": negative_prompt,
                    "width": 1024,
                    "height": 1024,
                    "model": "urn:air:sdxl:model:civitai:101055@128080", # SDXL Base
                    "additionalNetworks": {
                        f"urn:air:sdxl:lora:civitai:{model_id}": {
                            "strength": lora_config.get("strength", 0.75)
                        }
                    }
                }
            }
        }

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            logger.warning(f"CivitAI job submission failed: {response.text}")
            return None

        job_data = response.json()
        job_id = job_data.get("job", {}).get("id")
        if not job_id:
            return None

        # Poll CivitAI Orchestrator
        poll_url = f"https://orchestrator.civitai.com/v1/jobs/{job_id}"
        for _ in range(25):
            await asyncio.sleep(5)
            poll_resp = requests.get(poll_url, headers=headers)
            if poll_resp.status_code != 200:
                continue
            
            p_data = poll_resp.json()
            status = p_data.get("job", {}).get("status")
            if status == "Succeeded":
                image_url = p_data.get("job", {}).get("result", {}).get("images", [{}])[0].get("url")
                if image_url:
                    return LoRAInferenceRouter._download_image(image_url, output_path)
                break
            elif status == "Failed":
                logger.warning("CivitAI job failed.")
                break

        return None

    @staticmethod
    def _download_image(url: str, output_path: str) -> str:
        resp = requests.get(url)
        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(resp.content)
            return output_path
        return None

class ReferenceSheetGenerator:
    @staticmethod
    async def generate_references(character_config: dict, base_dir: Path) -> list:
        """
        Generates 10 starting reference images for LoRA training using Pollinations FLUX.
        Saves them to data/refs/{char_id}/
        """
        char_id = character_config.get("id")
        name = character_config.get("name")
        logger.info(f"Generating reference sheet for character '{name}'...")
        
        char_refs_dir = base_dir / char_id
        char_refs_dir.mkdir(parents=True, exist_ok=True)
        
        # 10 diverse scenes for character reference sheet
        prompts = [
            f"studio portrait of {name}, looking directly at camera, soft lighting",
            f"casual photo of {name} sitting in a local cafe, drinking coffee, looking out the window",
            f"medium shot of {name} working on a laptop at a gaming desk, cozy ambient lighting",
            f"action photo of {name} laughing, street style fashion, outdoor city street, daylight",
            f"close-up portrait of {name}, smiling, bokeh background",
            f"polaroid shot of {name} at a cozy home setup, warm tones, nostalgic style",
            f"candid photo of {name} thinking, looking up, holding a coffee mug",
            f"fashion model portrait of {name}, neutral background, professional studio lighting",
            f"cozy winter indoor photo of {name} wearing a large scarf, holding hot chocolate",
            f"cinematic shot of {name} walking in a neon cyberpunk street, reflections of lights in rain"
        ]
        
        saved_paths = []
        from src.generation.image import ImageGenerator
        
        for i, base_p in enumerate(prompts):
            prompt_data = ConsistencyPromptBuilder.build_prompt(character_config, base_p)
            prompt = prompt_data["prompt"]
            
            output_file = char_refs_dir / f"ref_{i+1:02d}.png"
            logger.info(f"Generating reference image {i+1}/10: '{base_p}'")
            try:
                path = await ImageGenerator.generate_ai_character_image(prompt, str(output_file))
                saved_paths.append(path)
            except Exception as e:
                logger.error(f"Failed to generate reference image {i+1}: {str(e)}")
                
        logger.info(f"Generated {len(saved_paths)} reference images in {char_refs_dir}")
        return saved_paths

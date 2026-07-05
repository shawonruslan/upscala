import sys
import io
# Force UTF-8 output on Windows consoles
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

"""
bulk_upscale.py
===============
Bulk image upscaler using VanceAI automation.

- Scans an input folder for images (PNG, JPG, JPEG, WEBP, BMP)
- Groups them into batches of CREDITS_PER_ACCOUNT (default: 5)
- For each batch: auto-registers a fresh VanceAI account (EduMails),
  logs in, and upscales each image one by one
- Saves all upscaled images to the output folder

Usage:
    python bulk_upscale.py --input ./input_images --output ./output_images
    python bulk_upscale.py --input ./input_images --output ./output_images --scale 4x --format jpg
"""

import asyncio
import os
import sys
import time
import json
import random
import string
import re
import html
import argparse
import shutil
from pathlib import Path

import requests
from PIL import Image
from playwright.async_api import async_playwright

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────
CREDITS_PER_ACCOUNT = 5           # Free credits per new VanceAI account
EDUMAILS_BASE       = "https://api.edu-mails.com/api"
SIGNUP_PASSWORD     = "Shawon63@@#VanceAI"  # Strong shared password for all accounts
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
FIRST_NAMES = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard",
    "Joseph", "Thomas", "Charles", "Mary", "Patricia", "Jennifer", "Linda",
    "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen", "Emily",
    "Amanda", "Ashley", "Megan", "Daniel", "Matthew", "Anthony", "Mark",
    "Donald", "Steven"
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson"
]


# ──────────────────────────────────────────────────────────────────────────────
# EduMails Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _api_request(method: str, url: str, max_retries: int = 5, **kwargs) -> requests.Response:
    """Make an HTTP request with automatic retry and exponential backoff."""
    kwargs.setdefault("timeout", 45)
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            wait = 2 ** attempt * 5   # 5s, 10s, 20s, 40s, 80s
            print(f"  [API] {method} {url} timed out (attempt {attempt+1}/{max_retries}). Retrying in {wait}s ...")
            time.sleep(wait)
        except requests.HTTPError as e:
            raise
    raise RuntimeError(f"API request failed after {max_retries} attempts: {method} {url}")

def edumails_get_domains() -> list:
    resp = _api_request("GET", f"{EDUMAILS_BASE}/domains")
    return resp.json()["data"]["domains"]

def edumails_generate_email(alias: str, domain_id: int) -> dict:
    payload = {"action": "custom", "alias": alias, "domain_id": domain_id}
    resp = _api_request("POST", f"{EDUMAILS_BASE}/emails/generate", json=payload)
    return resp.json()["data"]["email"]

def edumails_get_inbox(uuid: str) -> list:
    resp = _api_request("GET", f"{EDUMAILS_BASE}/emails/{uuid}")
    return resp.json()["data"].get("messages", [])

def random_alias() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"vanced{suffix}"

def extract_verification_link(body: str) -> str | None:
    if not body:
        return None
    # Priority 1: direct vanceai.com registration link visible in text
    direct = re.findall(r'https?://vanceai\.com/register\?[^\s\'"<>]+', body)
    if direct:
        return html.unescape(direct[0])
    # Priority 2: Mailgun tracking link
    tracking = re.findall(r'https?://email\.mg\.vanceai\.com/c/[^\s\'"<>]+', body)
    if tracking:
        return html.unescape(tracking[0])
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Account Registration (full pipeline)
# ──────────────────────────────────────────────────────────────────────────────

async def register_new_account() -> dict:
    """
    Creates a brand-new VanceAI account using EduMails temp email.
    Returns {"email": str, "password": str}
    """
    print("\n" + "="*60)
    print("  CREATING NEW VANCEAI ACCOUNT")
    print("="*60)

    # 1. Get a temp email
    domains = edumails_get_domains()
    if not domains:
        raise RuntimeError("EduMails: no domains available.")
    domain = domains[0]
    alias = random_alias()
    email_data = edumails_generate_email(alias, domain["id"])
    temp_email = email_data["address"]
    email_uuid = email_data["uuid"]
    print(f"  Temp email : {temp_email}")

    # 2. Browser: navigate to VanceAI → click Login → click Sign Up → send link
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800}, user_agent=USER_AGENT
        )
        page = await context.new_page()

        print("  Navigating to vanceai.com ...")
        await page.goto("https://vanceai.com/", wait_until="networkidle")

        # Click Log in
        await page.locator('button:has-text("Log in")').first.click()

        # Click Sign up
        signup_link = page.locator('button:has-text("Sign up"), a:has-text("Sign up")').last
        await signup_link.wait_for(state="visible", timeout=6000)
        await signup_link.click()

        # Enter email
        email_input = page.locator('input[type="email"]').first
        await email_input.wait_for(state="visible", timeout=5000)
        await email_input.fill(temp_email)

        # Click Send sign up link
        send_btn = page.locator(
            'button:has-text("Send sign up link"), button[type="submit"]:has-text("Send")'
        ).first
        await send_btn.wait_for(state="visible", timeout=5000)
        await send_btn.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        await page.wait_for_timeout(500)

        await context.close()
        await browser.close()

    # 3. Poll inbox for verification email
    print("  Polling inbox for activation email ...")
    verification_link = None
    for attempt in range(36):          # up to 3 minutes
        time.sleep(5)
        messages = edumails_get_inbox(email_uuid)
        if messages:
            link = extract_verification_link(messages[0].get("body", ""))
            if link:
                verification_link = link
                print(f"  Verification link found on attempt {attempt + 1}")
                break
        if attempt % 3 == 0:
            print(f"  Still waiting... ({attempt * 5}s)")

    if not verification_link:
        raise RuntimeError("No verification link received within 3 minutes.")

    # 4. Browser: open the verification link, fill the form, submit
    first_name = random.choice(FIRST_NAMES)
    last_name  = random.choice(LAST_NAMES)
    print(f"  Registering as: {first_name} {last_name}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800}, user_agent=USER_AGENT
        )
        page = await context.new_page()

        await page.goto(verification_link, wait_until="networkidle", timeout=25000)

        # Wait for form inputs to load
        try:
            await page.locator('input').first.wait_for(state="attached", timeout=8000)
        except Exception:
            pass

        # First name
        fn_input = page.locator(
            'input[placeholder*="First"], input[placeholder*="first"], input[autocomplete="given-name"]'
        ).first
        if await fn_input.is_visible():
            await fn_input.fill(first_name)
        else:
            text_inputs = await page.locator('input[type="text"]').all()
            if len(text_inputs) >= 2:
                await text_inputs[0].fill(first_name)
                await text_inputs[1].fill(last_name)

        # Last name
        ln_input = page.locator(
            'input[placeholder*="Last"], input[placeholder*="last"], input[autocomplete="family-name"]'
        ).first
        if await ln_input.is_visible():
            await ln_input.fill(last_name)

        # Passwords
        pwd_inputs = await page.locator('input[type="password"]').all()
        if len(pwd_inputs) >= 2:
            await pwd_inputs[0].fill(SIGNUP_PASSWORD)
            await pwd_inputs[1].fill(SIGNUP_PASSWORD)
        elif len(pwd_inputs) == 1:
            await pwd_inputs[0].fill(SIGNUP_PASSWORD)

        # Terms checkbox
        try:
            await page.locator('input[type="checkbox"]').first.click(force=True)
        except Exception:
            pass

        # Submit
        complete_btn = page.locator(
            'button:has-text("Complete sign up"), button[type="submit"]'
        ).first
        await complete_btn.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        await context.close()
        await browser.close()

    print(f"  Account created! Email: {temp_email}")
    return {"email": temp_email, "password": SIGNUP_PASSWORD}


async def dismiss_dialogs(page):
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    try:
        for close_sel in ['button[aria-label="Close"]', 'button:has-text("Close")', 'button:has-text("Maybe later")', 'text="Maybe later"']:
            close_btn = page.locator(close_sel).first
            if await close_btn.is_visible():
                await close_btn.click()
                await page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        await page.evaluate("""() => {
            const selectors = ['[role="dialog"]', '[class*="modal"]', '[class*="backdrop"]', '.fixed.inset-0.z-\\\\[950\\\\]'];
            selectors.forEach(sel => {
                try {
                    document.querySelectorAll(sel).forEach(el => el.remove());
                } catch(e) {}
            });
        }""")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Login and Upload + Upscale one image
# ──────────────────────────────────────────────────────────────────────────────

async def login_and_upscale_image(
    page,
    image_path: str,
    output_path: str,
    scale: str,
    fmt: str,
    email: str,
    password: str,
    already_logged_in: bool
) -> bool:
    """
    Using an already-open Playwright page, log in (if needed), go to My Studio,
    upload one image, upscale it, and save it to output_path.
    Returns True on success.
    """
    if not already_logged_in:
        print(f"\n  Logging in as {email} ...")
        await page.goto("https://vanceai.com/", wait_until="networkidle")

        # Click Log in
        login_btn = page.locator('button:has-text("Log in")').first
        await login_btn.wait_for(state="visible", timeout=8000)
        await login_btn.click()

        # Fill email + password
        email_input = page.locator('input[type="email"]').first
        await email_input.wait_for(state="visible", timeout=5000)
        await email_input.fill(email)

        pwd_input = page.locator('input[type="password"]').first
        await pwd_input.wait_for(state="visible", timeout=5000)
        await pwd_input.fill(password)

        submit_btn = page.locator('button[type="submit"]:has-text("Sign in")').first
        await submit_btn.click()

        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        print("  Login complete.")

        # Dismiss any welcome/onboarding dialogues
        await dismiss_dialogs(page)

        # Navigate to My Studio
        print(f"  Going to My Studio ...")
        studio_selectors = [
            'text="My studio"', 'a:has-text("My studio")',
            'button:has-text("My studio")', 'a[href*="/workspace"]', 'a[href*="/studio"]'
        ]
        
        # Wait dynamically for one of the selectors to become visible
        studio_loc = None
        for _ in range(20): # max 10 seconds wait (20 * 500ms)
            for sel in studio_selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.is_visible():
                        studio_loc = loc
                        break
                except Exception:
                    pass
            if studio_loc:
                break
            await page.wait_for_timeout(500)

        if studio_loc:
            await studio_loc.click()
        else:
            # Fallback to try clicking the first one anyway
            try:
                await page.locator(studio_selectors[0]).first.click()
            except Exception:
                pass

        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass

    # Upload image
    abs_image_path = os.path.abspath(image_path)
    print(f"  Uploading: {os.path.basename(image_path)}")
    try:
        # Try file chooser first as it simulates real user click and initializes upload handlers correctly
        trigger_selectors = [
            '[data-testid="workspace-upload-trigger"]',
            'text="Click to upload image"'
        ]
        upload_trigger = None
        for sel in trigger_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible():
                    upload_trigger = loc
                    break
            except Exception:
                continue
        if not upload_trigger:
            upload_trigger = page.locator('[data-testid="workspace-upload-trigger"]').first

        async with page.expect_file_chooser(timeout=3000) as fc_info:
            await upload_trigger.click(timeout=3000)
        file_chooser = await fc_info.value
        await file_chooser.set_files(abs_image_path)
        print("  Image uploaded successfully via file chooser.")
    except Exception as e:
        print(f"  File chooser method failed/timed out ({e}), trying direct input injection...")
        try:
            # Fallback: Directly inject into input[type="file"]
            file_input = page.locator('input[type="file"]').first
            await file_input.wait_for(state="attached", timeout=5000)
            await file_input.set_input_files(abs_image_path)
            print("  Image uploaded successfully via direct input.")
        except Exception as e2:
            print(f"  Both upload methods failed. Main error: {e2}")
            raise e2

    # Wait dynamically for upload network requests to settle
    print("  Waiting for upload network requests to settle...")
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)

    # Select scale
    print(f"  Selecting scale {scale} ...")
    try:
        scale_btn = page.get_by_text(scale, exact=True).first
        await scale_btn.wait_for(state="visible", timeout=6000)
        await scale_btn.click()
    except Exception as e:
        print(f"  Scale button not found: {e}")

    # Click Process
    print(f"  Clicking Process ...")
    process_btn = page.locator('button:has-text("Process")').first
    await process_btn.wait_for(state="visible", timeout=6000)
    await process_btn.click()

    # Wait for Save/Download button (up to ~60 seconds, checking every 1s)
    print(f"  Processing image ... (waiting for completion)")
    completed = False
    for i in range(60):
        await page.wait_for_timeout(1000)

        # Dismiss any upgrade/upsell modal
        for close_sel in ['button[aria-label="Close"]', 'button:has-text("Maybe later")', 'text="Maybe later"']:
            try:
                close_btn = page.locator(close_sel).first
                if await close_btn.is_visible():
                    await close_btn.click()
                    break
            except Exception:
                pass

        # Check for Save button
        save_btn = page.locator('button:has-text("Save"), button:has-text("Download")').first
        if await save_btn.is_visible():
            print(f"  Processing done after ~{i + 1}s!")
            completed = True
            break

    if not completed:
        print("  Warning: Processing may not be complete (Save button not found).")

    # Download
    print(f"  Downloading result ...")
    try:
        save_btn = page.locator('button:has-text("Save"), button:has-text("Download")').first
        await save_btn.wait_for(state="visible", timeout=10000)

        final_ext = "jpg" if fmt.lower() in ["jpg", "jpeg"] else "png"
        tmp_download = str(Path(output_path).parent / f"_tmp_download_{random.randint(1000, 9999)}")

        async with page.expect_download(timeout=25000) as dl_info:
            await save_btn.click()

            # Check if format selection popup appears dynamically
            jpg_sel = page.locator('text="JPG", text="jpg"').first
            png_sel = page.locator('text="PNG", text="png"').first
            
            format_popup_visible = False
            for _ in range(4): # check 4 times, 500ms each (max 2s)
                if await jpg_sel.is_visible() or await png_sel.is_visible():
                    format_popup_visible = True
                    break
                await page.wait_for_timeout(500)

            if format_popup_visible:
                if final_ext == "jpg" and await jpg_sel.is_visible():
                    await jpg_sel.click()
                elif final_ext == "png" and await png_sel.is_visible():
                    await png_sel.click()
                
                confirm = page.locator('button:has-text("Download"), button:has-text("Confirm"), button:has-text("Save")').last
                await confirm.click()

        download = await dl_info.value
        _, dl_ext = os.path.splitext(download.suggested_filename)
        tmp_path = tmp_download + dl_ext
        await download.save_as(tmp_path)

        # Convert format if needed
        if final_ext == "jpg" and dl_ext.lower() not in [".jpg", ".jpeg"]:
            with Image.open(tmp_path) as img:
                img.convert("RGB").save(output_path, "JPEG")
            os.remove(tmp_path)
        elif final_ext == "png" and dl_ext.lower() != ".png":
            with Image.open(tmp_path) as img:
                img.save(output_path, "PNG")
            os.remove(tmp_path)
        else:
            shutil.move(tmp_path, output_path)

        print(f"  Saved: {output_path}")
        return True

    except Exception as e:
        print(f"  Download failed: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ──────────────────────────────────────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

def collect_images(input_dir: str) -> list[str]:
    """Return sorted list of supported image file paths from input_dir."""
    all_files = []
    for f in sorted(Path(input_dir).iterdir()):
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            all_files.append(str(f))
    return all_files


async def bulk_upscale(
    input_dir: str,
    output_dir: str,
    scale: str = "2x",
    fmt: str = "png"
):
    images = collect_images(input_dir)
    if not images:
        print(f"No supported images found in '{input_dir}'. Exiting.")
        return

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs("logs", exist_ok=True)

    total = len(images)
    print(f"\n{'='*60}")
    print(f"  BULK UPSCALER - VanceAI Automation")
    print(f"{'='*60}")
    print(f"  Input folder  : {input_dir}")
    print(f"  Output folder : {output_dir}")
    print(f"  Scale         : {scale}")
    print(f"  Format        : {fmt.upper()}")
    print(f"  Total images  : {total}")
    print(f"  Credits/acct  : {CREDITS_PER_ACCOUNT}")
    print(f"  Accounts needed: {-(-total // CREDITS_PER_ACCOUNT)} (approx)")
    print(f"{'='*60}\n")

    # Log file to track progress
    log_path = "logs/bulk_upscale_log.json"
    progress_log = {"done": [], "failed": []}
    if os.path.exists(log_path):
        try:
            with open(log_path) as f:
                loaded = json.load(f)
                if isinstance(loaded, dict) and "done" in loaded and "failed" in loaded:
                    progress_log = loaded
                else:
                    print(f"  WARNING: Progress log {log_path} format is invalid. Re-initializing.")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  WARNING: Progress log {log_path} is empty, corrupted, or invalid JSON ({e}). Re-initializing progress tracker.")

    done_set = set(progress_log["done"])
    remaining = [img for img in images if os.path.basename(img) not in done_set]

    if not remaining:
        print("All images are already processed. Nothing to do!")
        return

    print(f"  Already done   : {len(done_set)}")
    print(f"  Remaining      : {len(remaining)}\n")

    processed_count = 0

    # Split remaining into batches of CREDITS_PER_ACCOUNT
    batches = [
        remaining[i : i + CREDITS_PER_ACCOUNT]
        for i in range(0, len(remaining), CREDITS_PER_ACCOUNT)
    ]

    for batch_idx, batch in enumerate(batches):
        print(f"\n{'-'*60}")
        print(f"  BATCH {batch_idx + 1}/{len(batches)} - {len(batch)} image(s)")
        print(f"{'-'*60}")

        # Register a fresh account for this batch
        try:
            credentials = await register_new_account()
        except Exception as e:
            print(f"  ERROR: Could not create account for batch {batch_idx + 1}: {e}")
            print("  Waiting 30 seconds before retrying...")
            await asyncio.sleep(30)
            try:
                credentials = await register_new_account()
            except Exception as e2:
                print(f"  Registration retry also failed: {e2}. Skipping batch.")
                for img in batch:
                    progress_log["failed"].append(os.path.basename(img))
                with open(log_path, "w") as f:
                    json.dump(progress_log, f, indent=2)
                continue

        email    = credentials["email"]
        password = credentials["password"]

        # Open a browser session for this batch
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800}, user_agent=USER_AGENT
            )
            page = await context.new_page()

            logged_in = False

            for img_idx, img_path in enumerate(batch):
                img_name = os.path.basename(img_path)
                base_name = Path(img_path).stem
                out_ext   = "jpg" if fmt.lower() in ["jpg", "jpeg"] else "png"
                out_path  = os.path.join(output_dir, f"{base_name}_upscaled_{scale}.{out_ext}")

                print(f"\n  [{processed_count + 1}/{total}] Processing: {img_name}")

                # For images 2–5 in the batch, we're already logged in and in Studio.
                # We need to go back to add another image (click + Add image button).
                if logged_in and img_idx > 0:
                    try:
                        add_image_btn = page.locator(
                            'button:has-text("Add image"), button:has-text("+ Add image"), [aria-label*="Add image"]'
                        ).first
                        if await add_image_btn.is_visible():
                            print("  Clicking '+ Add image' to load next image ...")
                            await add_image_btn.click()
                            await page.wait_for_timeout(500)
                    except Exception:
                        pass

                try:
                    success = await login_and_upscale_image(
                        page=page,
                        image_path=img_path,
                        output_path=out_path,
                        scale=scale,
                        fmt=fmt,
                        email=email,
                        password=password,
                        already_logged_in=logged_in
                    )
                    logged_in = True

                    if success:
                        processed_count += 1
                        progress_log["done"].append(img_name)
                        print(f"  [DONE] [{processed_count}/{total}]: {img_name}")
                    else:
                        progress_log["failed"].append(img_name)
                        print(f"  [FAIL]: {img_name}")

                except Exception as e:
                    import traceback
                    error_msg = f"  ERROR processing {img_name}: {e}\n{traceback.format_exc()}"
                    print(error_msg)
                    try:
                        os.makedirs("logs", exist_ok=True)
                        with open("logs/error_log.txt", "a", encoding="utf-8") as err_f:
                            err_f.write(error_msg + "\n")
                    except Exception:
                        pass
                    progress_log["failed"].append(img_name)

                # Save progress after each image
                with open(log_path, "w") as f:
                    json.dump(progress_log, f, indent=2)

                # Small gap between images in same session
                if img_idx < len(batch) - 1:
                    await asyncio.sleep(2)

            await context.close()
            await browser.close()

        print(f"\n  Batch {batch_idx + 1} complete.")

        # Brief pause between account creations to avoid rate limiting
        if batch_idx < len(batches) - 1:
            print("  Waiting 10 seconds before creating the next account ...")
            await asyncio.sleep(10)

    # Final summary
    print(f"\n{'='*60}")
    print(f"  BULK UPSCALE COMPLETE")
    print(f"{'='*60}")
    print(f"  Total processed : {len(progress_log['done'])}")
    print(f"  Failed          : {len(progress_log['failed'])}")
    if progress_log["failed"]:
        print(f"  Failed files    : {', '.join(progress_log['failed'])}")
    print(f"  Output saved to : {output_dir}")
    print(f"  Progress log    : {log_path}")
    print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Bulk upscale images using VanceAI (auto account rotation)"
    )
    parser.add_argument(
        "--input", "-i",
        default="./input_images",
        help="Folder containing images to upscale (default: ./input_images)"
    )
    parser.add_argument(
        "--output", "-o",
        default="./output_images",
        help="Folder to save upscaled images (default: ./output_images)"
    )
    parser.add_argument(
        "--scale", "-s",
        default="2x",
        choices=["2x", "4x", "8x"],
        help="Upscale factor (default: 2x)"
    )
    parser.add_argument(
        "--format", "-f",
        default="png",
        choices=["png", "jpg"],
        dest="fmt",
        help="Output image format (default: png)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(bulk_upscale(
        input_dir=args.input,
        output_dir=args.output,
        scale=args.scale,
        fmt=args.fmt
    ))

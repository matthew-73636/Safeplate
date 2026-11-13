import base64
import json
import re
from pathlib import Path

import anthropic
import requests
import streamlit as st

# The tested prompt. {allergens} gets replaced with the user's choices below.
PROMPT = """You are checking a food label for someone with food allergies.
Their allergens: {allergens}

Other names these allergens can hide under:
{hidden_names}

Look at the photo and respond ONLY with JSON in this format:
{
  "readable": true or false,
  "verdict": "AVOID" | "CAUTION" | "NO ALLERGENS FOUND" | "CAN'T TELL",
  "found": [{"allergen": "...", "label_text": "exact words from label"}],
  "warnings": ["any 'may contain' or 'processed in a facility' statements"],
  "notes": "one short sentence for the user",
  "barcode": "the digits printed under the barcode, or null"
}

Rules:
- Check hidden names (e.g. whey, casein, and lactose are milk; albumin is egg).
- "May contain" or shared-facility warnings mean CAUTION, not safe.
- If any of their allergens is clearly visible on the label, the verdict
  must be "AVOID", even if other parts of the label are blurry or cut off.
- If no allergen is found but a "may contain" or shared-facility warning
  for one of their allergens is clearly visible, the verdict must be
  "CAUTION", even if other parts of the label are blurry or cut off.
- If any part of the ingredient list is blurry, cut off, or missing, set
  "readable" to false. Only use "CAN'T TELL" when no allergen and no
  warning is found AND part of the ingredient list is unreadable. Never guess.
- If "readable" is false, use "notes" to tell the user to retake the photo
  so the whole ingredient list is clear.
- Only list ingredients you can actually see in the photo.
- In "found", write each allergen exactly as it appears in their list.
- Only fill in "barcode" if you can clearly read every digit under the
  barcode. If there's no barcode or any digit is unclear, use null."""

# Load the allergen knowledge file (hidden names + common sources).
# Path(__file__).parent means "the folder app.py is in", so this works
# no matter where you start the app from.
with open(Path(__file__).parent / "allergens.json") as f:
    ALLERGENS = json.load(f)


def hidden_names_text(allergens):
    """Build the hidden-names list for the prompt, one line per allergen."""
    lines = []
    for allergen in allergens:
        names = ALLERGENS[allergen]["hidden_names"].keys()
        lines.append(f"- {allergen}: {', '.join(names)}")
    return "\n".join(lines)


def explain_hidden_names(allergen, label_text):
    """Find hidden names in the label text and explain them,
    e.g. "Whey is a milk protein." """
    explanations = []
    for name, info in ALLERGENS.items():
        if name.lower() != allergen.lower():
            continue  # not the allergen Claude flagged, skip it
        for hidden_name, meaning in info["hidden_names"].items():
            # \b means "word edge", so "til" won't match inside "distilled".
            # (s|es)? also lets "mussel" match "mussels".
            pattern = r"\b" + re.escape(hidden_name) + r"(s|es)?\b"
            if re.search(pattern, label_text.lower()):
                explanations.append(f"{hidden_name.capitalize()} is {meaning}.")
    return explanations


def barcode_is_valid(barcode):
    """Check a barcode's last digit (the "check digit") to catch misreads.
    Every real barcode's last digit is calculated from the other digits."""
    if len(barcode) not in (8, 12, 13, 14):
        return False  # real food barcodes are 8, 12, 13, or 14 digits
    digits = [int(d) for d in barcode]
    body, check = digits[:-1], digits[-1]
    # Going right to left, multiply the digits by 3, 1, 3, 1, ... and add up.
    total = 0
    for i, d in enumerate(reversed(body)):
        total += d * (3 if i % 2 == 0 else 1)
    # The check digit is whatever brings the total up to a multiple of 10.
    return (10 - total % 10) % 10 == check


def lookup_barcode(barcode):
    """Look up a product in Open Food Facts. Returns the product info,
    or None if the barcode isn't in the database."""
    url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
    response = requests.get(
        url,
        # Only ask for the fields we use, so the reply is small and fast.
        params={"fields": "product_name,brands,allergens_tags,traces_tags"},
        # Open Food Facts asks apps to say who they are.
        headers={"User-Agent": "SafePlate/1.0 (student project)"},
        timeout=10,  # give up after 10 seconds instead of hanging forever
    )
    if response.status_code == 404:
        return None  # 404 means "not found"
    response.raise_for_status()  # turn any other error code into an exception
    data = response.json()
    if data.get("status") != 1:
        return None
    return data.get("product")


def matching_allergens(allergens, tags):
    """Return the user's allergens whose Open Food Facts tags are in `tags`."""
    matches = []
    for allergen in allergens:
        for tag in ALLERGENS[allergen]["off_tags"]:
            if tag in tags:
                matches.append(allergen)
                break  # one matching tag is enough for this allergen
    return matches


def show_database_result(product, allergens):
    """Show what Open Food Facts says about the user's allergens."""
    name = product.get("product_name") or "Unknown product"
    brand = product.get("brands") or ""
    st.write(f"**{name}**" + (f" ({brand})" if brand else ""))

    contains = matching_allergens(allergens, product.get("allergens_tags", []))
    may_contain = matching_allergens(allergens, product.get("traces_tags", []))

    if contains:
        st.error(f"⛔ Open Food Facts lists: {', '.join(contains)}")
    if may_contain:
        st.warning(f"⚠️ Open Food Facts says it may contain: {', '.join(may_contain)}")
    if not contains and not may_contain:
        # Never green: a database with no match is not proof of anything.
        st.info("Open Food Facts doesn't list your allergens for this product.")

    st.caption("Open Food Facts is a volunteer-run database and can be "
               "incomplete or out of date. The label in your hand is what counts.")

    # Some allergens (like corn) aren't tracked by Open Food Facts at all.
    not_tracked = [a for a in allergens if not ALLERGENS[a]["off_tags"]]
    if not_tracked:
        st.caption(f"Open Food Facts doesn't track {', '.join(not_tracked)}, "
                   "so only the photo check covers those.")


def find_recalls(brand):
    """Search openFDA for food recalls from this brand, newest first.
    Returns a list of recalls (empty if there are none)."""
    brand = brand.replace('"', "")  # quotes would break the search
    response = requests.get(
        "https://api.fda.gov/food/enforcement.json",
        params={
            # Match the brand in the company name OR the product description.
            "search": f'recalling_firm:"{brand}" product_description:"{brand}"',
            "sort": "report_date:desc",
            "limit": 100,
        },
        timeout=10,
    )
    if response.status_code == 404:
        return []  # openFDA answers 404 when nothing matches
    response.raise_for_status()
    return response.json().get("results", [])


def mentions_allergen(text, allergen):
    """True if the text mentions the allergen or one of its hidden names."""
    words = [allergen.lower().rstrip("s")]  # "Peanuts" -> "peanut"
    words += list(ALLERGENS[allergen]["hidden_names"].keys())  # "whey", ...
    for word in words:
        # \b = word edge, so "egg" matches "eggs" but not "nutmeg".
        if re.search(r"\b" + re.escape(word), text.lower()):
            return True
    return False


def show_recalls(product, allergens):
    """Show FDA recalls for this product's brand that mention the user's allergens."""
    brands = product.get("brands") or ""
    brand = brands.split(",")[0].strip()  # "Ferrero, Nutella" -> "Ferrero"
    if not brand:
        st.caption("Open Food Facts has no brand for this product, so we couldn't check FDA recalls.")
        return

    with st.spinner("Checking FDA recalls..."):
        try:
            recalls = find_recalls(brand)
        except requests.RequestException as e:
            st.error(f"Couldn't reach openFDA: {e}")
            return

    # Keep only recalls whose reason mentions one of the user's allergens.
    matches = []
    for recall in recalls:
        reason = recall.get("reason_for_recall", "")
        hits = [a for a in allergens if mentions_allergen(reason, a)]
        if hits:
            matches.append((recall, hits))

    if not matches:
        st.info(f"No FDA recalls found for {brand} that mention your allergens.")
        st.caption("openFDA only covers recalls in the US.")
        return

    st.warning(f"⚠️ FDA recalls for {brand} mention your allergens. "
               "Check whether your product is one of them.")
    for recall, hits in matches[:5]:  # show the 5 newest at most
        date = recall.get("report_date", "")  # e.g. "20250314"
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"  # -> "2025-03-14"
        st.write(f"**{date}** ({recall.get('status', '?')}): {', '.join(hits)}")
        st.caption(f"Company: {recall.get('recalling_firm', '?')}")
        st.caption(f"Product: {recall.get('product_description', '')[:300]}")
        st.caption(f"Reason: {recall.get('reason_for_recall', '')}")


def run_barcode_check(barcode, allergens):
    """Check the barcode, look it up, and show the result.
    Used for both typed barcodes and barcodes read from the photo."""
    if not barcode_is_valid(barcode):
        st.warning(f"{barcode} doesn't look like a real barcode. Double-check the digits.")
        return
    with st.spinner("Looking up the barcode..."):
        try:
            product = lookup_barcode(barcode)
        except requests.RequestException as e:
            st.error(f"Couldn't reach Open Food Facts: {e}")
            return  # stop this check, but keep the rest of the page
    if product is None:
        st.info("That barcode isn't in Open Food Facts. Use the photo check instead.")
    else:
        show_database_result(product, allergens)
        st.write("**FDA recall check**")
        show_recalls(product, allergens)


# @st.cache_data remembers the answer for each photo + allergen choice.
# Streamlit re-runs the whole script on every click, and without this,
# each click would send the same photo to Claude again (and cost money).
@st.cache_data(show_spinner=False)
def check_label(image_bytes, media_type, allergens):
    """Send the photo and prompt to Claude and return its reply as text."""
    # The API can't take raw image bytes, so we turn them into base64 text.
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    # We use .replace() instead of .format() because the prompt contains
    # { } braces for the JSON example, and .format() would choke on them.
    prompt = PROMPT.replace("{allergens}", ", ".join(allergens))
    prompt = prompt.replace("{hidden_names}", hidden_names_text(allergens))

    client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])
    response = client.beta.messages.create(
        model="claude-opus-5-5",
        max_tokens=16000,
        # If Claude's safety filter declines a request, this lets the API
        # retry on another Claude model automatically instead of giving up.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,  # usually "image/jpeg"
                        "data": image_data,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )

    # The reply is a list of "blocks". We only want the text ones.
    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text
    return text


def parse_result(text):
    """Turn Claude's reply into a Python dict, or return None if we can't."""
    # Sometimes the JSON comes wrapped in ```json ... ``` fences, so we just
    # grab everything from the first { to the last }.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def final_verdict(result):
    """Claude's verdict, after our safety rule. Used on screen and in texts."""
    verdict = result.get("verdict", "CAN'T TELL")
    # Safety rule: if part of the label is unreadable, the answer is
    # CAN'T TELL — unless Claude already spotted an allergen (AVOID)
    # or a "may contain" warning (CAUTION).
    if result.get("readable") is False and verdict not in ("AVOID", "CAUTION"):
        verdict = "CAN'T TELL"
    if verdict not in ("AVOID", "CAUTION", "NO ALLERGENS FOUND"):
        verdict = "CAN'T TELL"  # anything we don't recognize
    return verdict


def show_result(result):
    """Show the verdict in the right colored box, plus the evidence."""
    verdict = final_verdict(result)
    notes = result.get("notes", "")
    readable = result.get("readable")

    if verdict == "AVOID":
        st.error(f"⛔ AVOID — {notes}")
    elif verdict == "CAUTION":
        st.warning(f"⚠️ CAUTION — {notes}")
    elif verdict == "NO ALLERGENS FOUND":
        # Never say "SAFE" — we only report what we could see.
        st.success(f"✅ NO ALLERGENS FOUND — {notes}")
        st.caption("This is not a guarantee. Always double-check the label yourself.")
    else:
        # CAN'T TELL, or any verdict we don't recognize.
        st.warning(f"❓ CAN'T TELL — {notes or 'Try a clearer, closer photo of the whole ingredient list.'}")

    # AVOID or CAUTION on a partly unreadable label: there could be more allergens.
    if readable is False and verdict in ("AVOID", "CAUTION"):
        st.info("Part of the label was hard to read, so there may be other allergens too. Retake the photo to check the rest.")

    # Show the exact label words behind each flag so the user can check them.
    found = result.get("found", [])
    if found:
        st.subheader("What we found on the label")
        for item in found:
            allergen = item.get("allergen", "?")
            label_text = item.get("label_text", "")
            st.write(f"**{allergen}** — \"{label_text}\"")
            # If the flag came from a hidden name, explain it.
            for explanation in explain_hidden_names(allergen, label_text):
                st.caption(f"💡 {explanation}")

    warnings = result.get("warnings", [])
    if warnings:
        st.subheader("Label warnings")
        for warning in warnings:
            st.write(f"- \"{warning}\"")


# ---------- The page ----------
st.title("🍽️ SafePlate")

# The choices come straight from allergens.json, so adding an allergen
# to the file adds it here too.
allergens = st.multiselect("Pick your allergens", list(ALLERGENS.keys()))

camera_photo = st.camera_input("Take a photo of the ingredient label")
uploaded_photo = st.file_uploader(
    "...or upload a photo",
    type=["jpg", "jpeg", "png", "webp"],  # image types Claude can read
)

# Use the camera photo if there is one, otherwise the uploaded one.
photo = camera_photo or uploaded_photo

if photo:
    if not allergens:
        st.info("Pick at least one allergen first.")
    elif photo.size > 5 * 1024 * 1024:
        # Claude's limit is 5 MB per image. Phone photos can be bigger.
        st.warning("That photo is over 5 MB. Try a smaller photo or a screenshot of it.")
    else:
        with st.spinner("Reading the label..."):
            try:
                reply = check_label(photo.getvalue(), photo.type, allergens)
            except anthropic.APIError as e:
                st.error(f"Couldn't reach Claude: {e}")
                st.stop()

        result = parse_result(reply)
        if result is None:
            # Claude didn't send valid JSON, so we refuse to guess.
            st.warning("❓ CAN'T TELL — Something went wrong reading the result. Please try again.")
            with st.expander("Raw reply (for debugging)"):
                st.text(reply)
        else:
            show_result(result)

            # If Claude could read the barcode in the photo, look it up too.
            # str() because Claude might send the digits as a number.
            photo_barcode = "".join(ch for ch in str(result.get("barcode") or "") if ch.isdigit())
            if photo_barcode:
                st.subheader("Barcode check (from your photo)")
                st.caption(f"Barcode read from the photo: {photo_barcode}. "
                           "Make sure the product name below matches what you're holding.")
                run_barcode_check(photo_barcode, allergens)

# ---------- Barcode check (Open Food Facts) ----------
st.subheader("Barcode check")
barcode = st.text_input("Type the barcode number (optional)")
# Keep only the digits, so "0 12345 67890 5" becomes "012345678905".
barcode = "".join(ch for ch in barcode if ch.isdigit())

if barcode:
    if not allergens:
        st.info("Pick at least one allergen first.")
    else:
        run_barcode_check(barcode, allergens)

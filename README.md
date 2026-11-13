SafePlate
SafePlate checks food labels for people with food allergies. Pick your allergens, snap or upload a photo of an ingredient label, and it flags risky ingredients (including hidden names like whey or tahini), looks the product up by barcode, and checks FDA recalls.
Try it
Working demo: safeplate-s96rz9cykmpfxcf6tcrr4f.streamlit.app
Demo video: https://youtu.be/gBKgTjsX6CQ
How to test it
Open the demo link and pick one or more allergens (for example, Milk and Tree nuts).
Upload a photo of an ingredient label (or use your camera). Any food label works, including one from a Google Images search.
You should see a colored verdict (AVOID, CAUTION, NO ALLERGENS FOUND, or CAN'T TELL), the exact label words that triggered it, and a 💡 explanation if it found a hidden name (for example, "Whey is a milk protein.").
If the barcode is clear in the photo, SafePlate reads it and shows a Barcode check with the product from Open Food Facts and any FDA recalls for that brand that mention your allergens.
No photo handy? Scroll to Barcode check, pick Milk, and type 3017620422003 (Nutella). You should see the product, a red "Open Food Facts lists: Milk", and an FDA recall check.
Track
Social Good
How it works
Trigger: the user picks their allergens and takes or uploads a photo of a food label.
AI step: Claude reads the label photo, checks every ingredient against the user's allergens and their hidden names, and returns a structured verdict with the exact label text it relied on. It also reads the barcode digits.
Output: a color-coded verdict with the label words that triggered it, explanations of hidden names, the product's allergen data from Open Food Facts, and matching FDA recalls.
Safety rules built in: SafePlate never says food is "safe". If part of the label is blurry or cut off and nothing was found, it says CAN'T TELL instead of guessing. If an allergen is clearly visible, it says AVOID even when part of the label is unreadable, and asks for a retake. Barcodes must pass a check-digit test before they're looked up, so a misread digit can't pull up the wrong product.
Superpowers / Integrations
Vision: Claude (Anthropic API) reads the ingredient label and the barcode digits from the photo.
Knowledge: SafePlate answers from its own allergen file (allergens.json): 13 allergens (the top 9 plus coconut, mustard, celery, and corn), each with hidden names and plain-language explanations. The hidden names for the user's allergens go into Claude's instructions, and the explanations shown on screen come straight from the file.
Real-world data: live product data from the Open Food Facts API (by barcode) and live food recall data from the FDA's openFDA API.
Why it matters to me
I've had food allergies my whole life, and it's been tough. Every snack, meal, or new product means carefully reading the label, and allergens often hide under names most people wouldn't recognize. I built SafePlate so people like me can check a label quickly and see exactly why something is flagged.
Built with
Python, Streamlit, Claude API (Anthropic) for vision, Open Food Facts API, openFDA API, Claude Code. Deployed on Streamlit Community Cloud.
Built by
Matthew Moller# Safeplate

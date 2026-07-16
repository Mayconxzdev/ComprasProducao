"""
Seed Product Memory from History DB
Parses existing quotes history to populate the initial product memory.
"""
import sys
import os
import re
import logging

# Add project root to path
sys.path.append(os.getcwd())

from app.core.product_memory import ProductMemory
from app.core import history_db

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("Seeder")

def parse_line_item(line: str) -> tuple:
    """
    Heuristically parse "QTD - PRODUTO TIPO ESP - MEDIDA"
    Returns (product, type, thickness, measure)
    """
    parts = line.split(" - ")

    # Needs at least 2 parts (Qty - Prod...)
    if len(parts) < 2:
        return None, None, None, None

    # Qty is parts[0]

    # Middle part (Product Type Thickness)
    middle = parts[1].strip()
    middle_words = middle.split()

    if not middle_words:
        return None, None, None, None

    # Heuristic: First word is Product
    product = middle_words[0]

    # Rest is Type + Thickness. Hard to separate without prior knowledge.
    # Let's put everything else in Type for now, and leave Thickness empty.
    # Or try to detect thickness (digits + mm/pol)?
    type_tokens = []
    thick_tokens = []

    for word in middle_words[1:]:
        # Simple regex for thickness like 3mm, 1/4", 2.5mm
        if re.search(r'\d+([.,]\d+)?(mm|cm|pol|")', word, re.IGNORECASE):
            thick_tokens.append(word)
        else:
            type_tokens.append(word)

    type_ = " ".join(type_tokens)
    thickness = " ".join(thick_tokens)

    # Measure is parts[2] if exists
    measure = ""
    if len(parts) > 2:
        measure = parts[2].strip()

    return product, type_, thickness, measure

def seed():
    logger.info("Initializing Product Memory...")
    memory = ProductMemory()
    memory.load()

    logger.info("Connecting to History DB...")
    try:
        conn = history_db.connect()
    except Exception as e:
        logger.error(f"Could not connect to DB: {e}")
        return

    logger.info("Reading quote items...")
    try:
        cur = conn.cursor()
        cur.execute("SELECT line_text FROM quote_items")
        rows = cur.fetchall()

        count = 0
        skipped = 0

        for row in rows:
            line = row[0]
            prod, type_, thick, measure = parse_line_item(line)

            if prod:
                memory.learn(prod, type_, thick, measure)
                count += 1
            else:
                skipped += 1

        logger.info(f"Processed {len(rows)} items.")
        logger.info(f"Learned: {count}")
        logger.info(f"Skipped: {skipped}")

        memory.save()
        logger.info("✅ Memory saved to NAS successfully!")

    except Exception as e:
        logger.error(f"Error seeding: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    seed()

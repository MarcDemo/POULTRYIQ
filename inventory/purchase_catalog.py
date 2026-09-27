PURCHASE_CATALOG = [
    {
        "code": "FEED",
        "name": "Feed",
        "items": [
            {"name": "Maize bran", "unit": "kg"},
            {"name": "Concentrate", "unit": "kg"},
            {"name": "Layer mash", "unit": "kg"},
            {"name": "Chick mash", "unit": "kg"},
            {"name": "Grower mash", "unit": "kg"},
            {"name": "Broiler starter", "unit": "kg"},
            {"name": "Broiler finisher", "unit": "kg"},
            {"name": "Soya meal", "unit": "kg"},
            {"name": "Fish meal", "unit": "kg"},
            {"name": "Sunflower cake", "unit": "kg"},
            {"name": "Cotton seed cake", "unit": "kg"},
            {"name": "Limestone", "unit": "kg"},
            {"name": "DCP", "unit": "kg"},
            {"name": "Premix", "unit": "kg"},
            {"name": "Salt", "unit": "kg"},
        ],
    },
    {
        "code": "DRUG",
        "name": "Drugs & Vaccines",
        "items": [
            {"name": "Newcastle vaccine", "unit": "dose"},
            {"name": "Gumboro vaccine", "unit": "dose"},
            {"name": "Fowl pox vaccine", "unit": "dose"},
            {"name": "Dewormer", "unit": "bottle"},
            {"name": "Antibiotics", "unit": "pack"},
            {"name": "Vitamins", "unit": "pack"},
            {"name": "Coccidiostat", "unit": "pack"},
            {"name": "Disinfectant", "unit": "litre"},
        ],
    },
    {
        "code": "BEDDING",
        "name": "Bedding & Litter",
        "items": [
            {"name": "Coffee husks", "unit": "bag"},
            {"name": "Rice husks", "unit": "bag"},
            {"name": "Wood shavings", "unit": "bag"},
            {"name": "Sawdust", "unit": "bag"},
        ],
    },
    {
        "code": "CONSUMABLE",
        "name": "Consumables",
        "items": [
            {"name": "Gloves", "unit": "box"},
            {"name": "Masks", "unit": "box"},
            {"name": "Detergent", "unit": "pack"},
            {"name": "Sanitizer", "unit": "litre"},
            {"name": "Brooms", "unit": "piece"},
            {"name": "Record books", "unit": "piece"},
            {"name": "Syringes", "unit": "pack"},
        ],
    },
    {
        "code": "BIRDS",
        "name": "Birds",
        "items": [
            {"name": "Day-old chicks", "unit": "bird"},
            {"name": "Point-of-lay birds", "unit": "bird"},
            {"name": "Replacement layers", "unit": "bird"},
        ],
    },
]


def purchase_catalog_items():
    rows = []
    for category in PURCHASE_CATALOG:
        for item in category["items"]:
            rows.append(
                {
                    "category_code": category["code"],
                    "category_name": category["name"],
                    "name": item["name"],
                    "unit": item["unit"],
                }
            )
    return rows


def purchase_catalog_item_names():
    return [item["name"] for item in purchase_catalog_items()]


def find_purchase_catalog_item(name):
    normalised = (name or "").strip().lower()
    for item in purchase_catalog_items():
        if item["name"].lower() == normalised:
            return item
    return None


def purchase_catalog_category(code):
    normalised = (code or "").strip().upper()
    for category in PURCHASE_CATALOG:
        if category["code"] == normalised:
            return category
    return None
